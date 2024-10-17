#!/usr/bin/python3

from flask import Flask, request, jsonify
from mastodon import Mastodon
import click
import os
import yaml
import traceback
import hashlib
import datetime
import requests
import threading
import time

DEFAULT_SCOPES = ['read', 'write', 'follow', 'push']
ADMIN_SCOPES = DEFAULT_SCOPES + ['admin:read', 'admin:write', 'admin:write:accounts']
MEDIAPATH = "/opt/mastobot/media"
SECRETPATH = "/opt/mastobot/secrets"
IDEMPATH = "/opt/mastobot/idempotency"
USERS_ENDPOINT = "/api/v1/pleroma/admin/users"
HARD_CODED_PASSWORD = "this-is-the-password-for-retoots"

task_runner = None

os.makedirs(SECRETPATH, exist_ok=True)
os.makedirs(IDEMPATH, exist_ok=True)

app = Flask(__name__)

# #################################################
# Class for repeated execution of gatherer
# #################################################

class RepeatedTask:
    def __init__(self, target_function, remote_mastodon, limit=1):
        self.target_function = target_function
        self.limit = limit
        self.remote_mastodon = remote_mastodon
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        """Start the task in a separate thread."""
        if self.thread is None:
            self.thread = threading.Thread(target=self._run)
            self.thread.start()

    def stop(self):
        """Stop the task and join the thread."""
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join()
            self.thread = None

    def _run(self):
        """Execute the task repeatedly with variating sleep times."""
        x = 0
        while True:
            if self.stop_event.is_set():
                break  # Exit if stop is triggered

            # Perform the task
            self.target_function(self.remote_mastodon, limit=self.limit)

            # Calculate sleep time based on your formula
            sleep_time = 10 + ((1234 + (x % 40) ** 3) % 71)
            time.sleep(sleep_time)

            # Increment x
            x += 1

# #################################################
# Download avatar from remote mastodon
# #################################################

def download_avatar(avatar_url, username):
    avatar_path = os.path.join(MEDIAPATH, f"{username}.png")
    try:
        response = requests.get(avatar_url)
        if response.status_code == 200:
            with open(avatar_path, 'wb') as f:
                f.write(response.content)
            print(f"Downloaded avatar for {username}")
        else:
            print(f"Failed to download avatar for {username}")
        return avatar_path
    except Exception as e:
        print(f"Error downloading avatar for {username}: {e}")
        return None

# #################################################
# Download avatar from this-person-does-not-exist
# #################################################

def generate_avatar(username, gender="all"):
    response = requests.get(f"https://this-person-does-not-exist.com/new?gender={ gender }&age=all&etnic=all")
    if response.status_code == 200:
        json_data = response.json()
        if json_data.get('generated'):
            image_url = f"https://this-person-does-not-exist.com{ json_data['src'] }"
            image_response = requests.get(image_url)
            
            if image_response.status_code == 200:
                avatar_path = os.path.join(MEDIAPATH, f"{username}.png")
                with open(avatar_path, 'wb') as f:
                    f.write(image_response.content)
                print(f"Image downloaded and saved as: {avatar_path}")
            else:
                print(f"Failed to download image: {image_response.status_code}")
    else:
        print(f"Failed to generate image: {response.status_code}")
    return

# #################################################
# Download from remote mastodon or generate one
# #################################################

def create_avatar(username = "", avatar_path = None, gender = "all"):
    if not avatar_path:
        generate_avatar(username, gender=gender)
    elif avatar_path.startswith('http'):
        download_avatar(avatar_path, username)
    else:
        pass

# #################################################
# Helper functions
# #################################################

@app.before_first_request
def before_first_request():
    """Ensure config is loaded before the first request."""
    load_config()

def load_config():
    global API_URL, PLEROMA_INITIAL_USERS, ADMIN, HASHTAGS, REMOTE_MASTODON_USER, REMOTE_MASTODON_PASSWORD

    with open("/opt/mastobot/config.yaml") as f:
        config = yaml.safe_load(f)
    
    API_URL = config.get('api_url')
    PLEROMA_INITIAL_USERS = config.get('users', [])
    HASHTAGS = config.get('remote_mastodon_hashtags', ['cybersecurity', 'infosec', 'hacked', 'cyberwarfare', 'hackernews', 'deepfake'])
    ADMIN = {
        "user": config.get('admin_user', 'admin'),
        "email": config.get('admin_email', 'admin@cyberrange.at'),
        "password": config.get('admin_password', 'adminpass')
    }

    REMOTE_MASTODON_USER = config.get('remote_mastodon_user')
    REMOTE_MASTODON_PASSWORD = config.get('remote_mastodon_password')

    print("*** Config loaded ***")

def login_admin():
    return login_user(
        ADMIN.get('user'),
        ADMIN.get('password'),
        scopes=ADMIN_SCOPES
    )

def login_remote():
    return login_user(
        REMOTE_MASTODON_USER, 
        password=REMOTE_MASTODON_PASSWORD, 
        scopes=DEFAULT_SCOPES, 
        api_url="https://mastodon.social"
    )

def get_existing_nicknames(admin_mastodon):
    existing_users = admin_mastodon._Mastodon__api_request('GET', USERS_ENDPOINT)
    return [
        user['nickname'] for user in existing_users['users']
        if user['nickname'] != ADMIN.get('user')
    ]


def find_post_by_content(unique_substring):   
    admin_mastodon = login_user( ADMIN.get('user'), ADMIN.get('password'))
    all_statuses = admin_mastodon._Mastodon__api_request('GET', '/api/v1/timelines/public')
    status_ids = [status['id'] for status in all_statuses if unique_substring in status['content']]
    if status_ids:
        return status_ids[0] 
    else:
        return None

# #################################################
# Post gatherer from remote machine
# #################################################

def get_posts_from_remote_mastodon(remote_mastodon = None, limit=15):
    posts = []
    for hashtag in HASHTAGS:
        new_posts = remote_mastodon.timeline_hashtag(hashtag, limit=limit)  or []
        for post in new_posts:
            posts.append(post)
    
    if not posts:
        print("No new posts found.")
        return

    admin_mastodon = login_admin()
    nicknames = get_existing_nicknames(admin_mastodon)

    for post in posts:
        try:
            username = post.get('account').get('username')
            email = f"{username}@cyberrange.at"
            content = post.get('content')

            if username in nicknames:
                print(f"*** trying to login as { username }***")
                mastodon = login_user(username=username, password=HARD_CODED_PASSWORD)
            else:
                print(f"*** trying to register as { username }***")
                avatar_path = post.get('account').get('avatar')
                mastodon = register_user(username=username, email=email, password=HARD_CODED_PASSWORD, avatar_path=avatar_path)

            if toot(mastodon, text=content):
                break

        except Exception as err:
            print("[{0}] ERROR {1}".format(username, err))
            traceback.print_exc()

# #################################################
# Create new post
# #################################################

def toot(mastodon, text="", content_type="text/html", media="", scheduled_at=None, idempotency_key=None, in_reply_to_id=None):
    
    if not idempotency_key:
        idempotency_key = hashlib.md5(mastodon.me()['id'].encode('utf-8') + text.encode('utf-8')).hexdigest() + "00"

    idem_path = f"{IDEMPATH}/{idempotency_key}"
    if os.path.isfile(idem_path):
        return False

    media_id = None
    media_path = f"{MEDIAPATH}/{media}"
    if media != "" and os.path.isfile(media_path):
        media_id = mastodon.media_post(media_file=media_path)
    
    mastodon.status_post(
            text,
            content_type=content_type, 
            media_ids=media_id,
            scheduled_at=scheduled_at, 
            idempotency_key=idempotency_key, 
            in_reply_to_id=in_reply_to_id
        )
    
    with open(idem_path, 'a'):
        os.utime(idem_path, None)

    return True

# #################################################
# Create reply to post
# #################################################

def reply_to_toot( username, password = None, reply_text = None, search_string = None, media=None):
    status_id = find_post_by_content(search_string)
    reply_mastodon = login_user(username, password=password)
    return toot(reply_mastodon, text=reply_text, in_reply_to_id=status_id, media=media)

# #################################################
# Init mastodon application
# #################################################

def create_app(username, secret_file, scopes = DEFAULT_SCOPES, api_url = None):

    if api_url is None:
        api_url = API_URL

    Mastodon.create_app(
        f"{username}_bot",
        api_base_url=api_url,
        to_file=secret_file,
        scopes=scopes
    )

    return Mastodon(
        client_id=secret_file,
        api_base_url=api_url,
        feature_set="pleroma"
    )


def login_user(username, password = None, scopes = DEFAULT_SCOPES, api_url = None):
    
    if api_url is None:
        api_url = API_URL

    secret_file = f"{SECRETPATH}/{username}"
    mastodon = create_app(username, secret_file, scopes, api_url)

    if not password:
        for default_user in PLEROMA_INITIAL_USERS:
            if default_user.get('login') == username:
                password = default_user.get('password')
                break

    mastodon.log_in(
        username=username,
        password=password,
        to_file=secret_file,
        scopes=scopes
    )
    return mastodon


def register_user(username, email, password, gender = "all", avatar_path = None):
    secret_file = f"{SECRETPATH}/{username}"

    if os.path.isfile(secret_file):
        return login_user(username, password)

    mastodon = create_app(username, secret_file)
    mastodon.create_account(
        username=username,
        password=password,
        email=email,
        agreement=True,
        to_file=secret_file
    )
    
    create_avatar( username = username, avatar_path = avatar_path, gender = gender)
    local_avatar_path = avatar_path
    if avatar_path.startswith('http'):
        local_avatar_path = f"{ MEDIAPATH }/{ username }.png"
    mastodon.account_update_credentials(avatar=local_avatar_path)

    print(f'user { username } created')
    return mastodon

def update_account(mastodon, account):
  if 'display_name' in account:
    mastodon.account_update_credentials(display_name=account['display_name'])
  if 'bio' in account:
    mastodon.account_update_credentials(note=account['bio'])
  if 'header' in account:
    mastodon.account_update_credentials(header=f"{MEDIAPATH}/{account['header']}")

# #################################################
# Init function
# #################################################

def init_function():
    admin_mastodon = login_admin()
    nicknames = get_existing_nicknames(admin_mastodon)

    for user in PLEROMA_INITIAL_USERS:
        try:
            if user.get('login') in nicknames:
                print(f"*** trying to login as { user.get('login') }***")
                mastodon = login_user(
                    username=user.get('login'),
                    password=user.get('password', None)
                )
            else:
                print(f"*** trying to register as { user.get('login') }***")
                avatar_path=f"{ MEDIAPATH }/{ user.get('account', None).get('avatar', None) }"
                mastodon = register_user(
                    username=user.get('login'),
                    email=user.get('email'),
                    password=user.get('password', None),
                    avatar_path=avatar_path
                )

            print(f"*** updating account for { user['login'] }***")
            update_account(mastodon, user.get('account', []))

            print(f"*** initialize toots for { user['login'] }***")
            initialize_toots(mastodon, user.get('initial_toots', []))
            
            print(f"*** initialize follows for { user['login'] }***")
            initialize_follows(mastodon, nicknames, user.get('follow', []))

        except Exception as err:
            print("[{0}] ERROR {1}".format(user['login'], err))
            traceback.print_exc()

def initialize_toots(mastodon, initial_toots=[]):
  for initial_toot in initial_toots:
    idempotency = hashlib.md5(mastodon.me()['id'].encode('utf-8') + initial_toot['text'].encode('utf-8')).hexdigest()
    media_id, schedule = None, None
    if 'media' in initial_toot and os.path.isfile(f"{MEDIAPATH}/{initial_toot['media']}"):
      media_id = mastodon.media_post(media_file=f"{MEDIAPATH}/{initial_toot['media']}")
    if 'schedule' in initial_toot:
      schedule = datetime.datetime.now() + datetime.timedelta(minutes=initial_toot['schedule'])
    toot(mastodon, text=initial_toot['text'], media=media_id, scheduled_at=schedule, idempotency_key=idempotency)

def initialize_follows(mastodon, nicknames, follow=[]):
  for uid in follow:
    if uid in nicknames:
      mastodon.follows(uri=uid)

# Endpoint to start the population task
@app.route('/start_population', methods=['POST'])
def start_population():
    global task_runner
    try:
        load_config()
        remote_mastodon = login_remote()

        if task_runner is not None:
            return jsonify({"error": "Task is already running."}), 400

        task_runner = RepeatedTask(get_posts_from_remote_mastodon, remote_mastodon=remote_mastodon, limit=15)
        task_runner.start()

        return jsonify({"success": True, "message": "Population task started."}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# Endpoint to stop the population task
@app.route('/stop_population', methods=['POST'])
def stop_population():
    global task_runner
    try:
        if task_runner is None:
            return jsonify({"error": "No task is currently running."}), 400

        task_runner.stop()
        task_runner = None

        return jsonify({"success": True, "message": "Population task stopped."}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# Flask route to handle posting
@app.route('/post', methods=['POST'])
def post_toot():
    try:
        # Get JSON data from the request
        data = request.json

        username = data.get('username')
        password = data.get('password', "")
        text = data.get('text')
        media = data.get('media', "")

        # Login and toot
        mastodon = login_user(username, password)
        toot_response = toot(mastodon, text, media)
        
        return jsonify({"success": True, "toot_url": toot_response.url}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/reply', methods=['POST'])
def post_reply():
    try:
        data = request.json

        post_identifier = data.get('post_identifier')
        username = data.get('username')
        password = data.get('password', "")
        text = data.get('text')
        media = data.get('media', "")

        
        toot_response = reply_to_toot(
           username, 
           password = password, 
           reply_text = text, 
           search_string = post_identifier, 
           media=media
        )
        
        return jsonify({"success": True, "toot_url": toot_response.url}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@click.group()
def cli():
    """Mastobot command line interface."""
    load_config()

@cli.command()
def server():
    """Run the Flask server."""
    print("Starting Flask server...")
    app.run(host='0.0.0.0', port=5000)

@cli.command()
def init():
    """Initialize the bot."""
    init_function()

@cli.command()
def populate():
    """Populate by repeatedly fetching posts from official Mastodon."""
    load_config()
    remote_mastodon = login_remote()
    task_runner = RepeatedTask(get_posts_from_remote_mastodon, remote_mastodon=remote_mastodon, limit=15)

    print("Starting repeated fetching of posts...")
    task_runner.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping repeated fetching of posts...")
        task_runner.stop()

if __name__ == '__main__':
    cli()
