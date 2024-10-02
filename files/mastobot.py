#!/usr/bin/python3

import click
from mastodon import Mastodon
import os
import yaml
import traceback
import hashlib
import datetime
from flask import Flask, request, jsonify

DEFAULT_SCOPES = ['read', 'write', 'follow', 'push']
ADMIN_SCOPES = DEFAULT_SCOPES + ['admin:read', 'admin:write', 'admin:write:accounts']
MEDIAPATH = "/opt/mastobot/media"
SECRETPATH = "/opt/mastobot/secrets"
USERS_ENDPOINT = "/api/v1/pleroma/admin/users"

# Initialize Flask
app = Flask(__name__)

def load_config():
    """Load the configuration from the YAML file."""
    global API_URL, USERS, ADMIN

    with open("/opt/mastobot/config.yaml") as f:
        config = yaml.safe_load(f)
    
    API_URL = config.get('api_url')
    USERS = config.get('users', [])
    ADMIN = {
        "user": config.get('admin_user', 'admin'),
        "email": config.get('admin_email', 'admin@cyberrange.at'),
        "password": config.get('admin_password', 'adminpass')
    }

    print("*** Config loaded ***")

@app.before_first_request
def before_first_request():
    """Ensure config is loaded before the first request."""
    load_config()
    
def toot(mastodon, text="", media="", scheduled_at=None, idempotency_key=None, in_reply_to_id=None):
    
    if not idempotency_key:
        idempotency_key = hashlib.md5(mastodon.me()['id'].encode('utf-8') + text.encode('utf-8')).hexdigest() + "00"

    media_id = None
    media_path = f"{MEDIAPATH}/{media}"
    if media != "" and os.path.isfile(media_path):
        media_id = mastodon.media_post(media_file=media_path)
    
    return mastodon.status_post(
       text, 
       media_ids=media_id,
       scheduled_at=scheduled_at, 
       idempotency_key=idempotency_key, 
       in_reply_to_id=in_reply_to_id
    )

def find_post_by_content(unique_substring):   
    admin_mastodon = login_user( ADMIN.get('user'), ADMIN.get('password'))
    all_statuses = admin_mastodon._Mastodon__api_request('GET', '/api/v1/timelines/public')
    status_ids = [status['id'] for status in all_statuses if unique_substring in status['content']]
    return status_ids[0] or None

def reply_to_toot( username, password = None, reply_text = None, search_string = None, media=None):
    status_id = find_post_by_content(search_string)
    reply_mastodon = login_user(username, password=password)
    return toot(reply_mastodon, text=reply_text, in_reply_to_id=status_id, media=media)

def create_app(username, secret_file, scopes=DEFAULT_SCOPES):
    Mastodon.create_app(
        f"{username}_bot",
        api_base_url=API_URL,
        to_file=secret_file,
        scopes=scopes
    )

    return Mastodon(
        client_id=secret_file,
        api_base_url=API_URL,
        feature_set="pleroma"
    )

def login_user(username, password = None, scopes = DEFAULT_SCOPES):
    secret_file = f"{SECRETPATH}/{username}"
    mastodon = create_app(username, secret_file, scopes)

    if not password:
        for default_user in USERS:
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


def register_user(username, email, password):
  secret_file = f"{SECRETPATH}/{username}"

  if os.path.isfile(secret_file):
    return login_user(username, email, password)

  mastodon = create_app(username, secret_file)
  mastodon.create_account(
      username=username,
      password=password,
      email=email,
      agreement=True,
      to_file=secret_file
  )

  print(f'user { username } created')
  return mastodon


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


def update_account(mastodon, account):
  if 'display_name' in account:
    mastodon.account_update_credentials(display_name=account['display_name'])
  if 'bio' in account:
    mastodon.account_update_credentials(note=account['bio'])
  if 'avatar' in account:
    mastodon.account_update_credentials(avatar=f"{MEDIAPATH}/{account['avatar']}")
  if 'header' in account:
    mastodon.account_update_credentials(header=f"{MEDIAPATH}/{account['header']}")


def init_function():
    print("*** Init ***")

    os.makedirs(SECRETPATH, exist_ok=True)

    admin_mastodon = login_user(
        ADMIN.get('user'),
        ADMIN.get('password'),
        scopes=ADMIN_SCOPES
    )

    # Get all existing users
    existing_users = admin_mastodon._Mastodon__api_request('GET', USERS_ENDPOINT)
    nicknames = [existing_user['nickname'] for existing_user in existing_users['users'] if existing_user['nickname'] != ADMIN.get('user')]

    for user in USERS:
        try:
            if user.get('login') in nicknames:
                print(f"*** trying to login as { user.get('login') }***")
                mastodon = login_user(
                    username=user.get('login'),
                    password=user.get('password', None)
                )
            else:
                print(f"*** trying to register as { user.get('login') }***")
                mastodon = register_user(
                    username=user.get('login'),
                    email=user.get('email'),
                    password=user.get('password', None)
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


def reset_function():

    admin_mastodon = login_user(
        ADMIN.get('user'),
        ADMIN.get('password'),
        scopes=ADMIN_SCOPES
    )

    # Get all existing users
    existing_users = admin_mastodon._Mastodon__api_request('GET', USERS_ENDPOINT)
    nicknames = [existing_user['nickname'] for existing_user in existing_users['users'] if existing_user['nickname'] != ADMIN.get('user')]

    for nickname in nicknames:
        user_mastodon = login_user(nickname)
        uid = user_mastodon.me()['id']

        # Remove all toots of user
        toots = user_mastodon.account_statuses(uid)
        for toot in toots:
            user_mastodon.status_delete(toot['id'])
            print(f"  * removed toot { toot['id'] }")

        try:
            # Remove secret file
            os.remove(f"{ SECRETPATH }/{ nickname }.secret")
            print(f"  * removed scret file for user '{ nickname }'")
        except:
            pass

        # FIXME:
        # # Remove users
        # admin_mastodon._Mastodon__api_request('DELETE', USERS_ENDPOINT, { "nicknames" : nicknames })
        # print(f"  * removed users '{ nicknames }'")

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
        # Get JSON data from the request
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
def reset():
    """Reset the bot configuration."""
    reset_function()

if __name__ == '__main__':
    cli()
