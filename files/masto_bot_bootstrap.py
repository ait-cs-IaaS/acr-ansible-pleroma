#!/usr/bin/python3

from mastodon import Mastodon
import os
import click
import yaml
import traceback
import datetime
import hashlib

API_URL = ""
MEDIAPATH = "/opt/mastodon/media"
USERS_ENDPOINT = "/api/v1/pleroma/admin/users"
DEFAULT_SCOPES = ['read', 'write', 'follow', 'push']
ADMIN_SCOPES = DEFAULT_SCOPES + ['admin:read', 'admin:write']


def initialize_toots(mastodon, initial_toots=[]):
  for toot in initial_toots:
    idempotency = hashlib.md5(mastodon.me()['id'].encode('utf-8') + toot['text'].encode('utf-8')).hexdigest()
    media_id, schedule = None, None
    if 'media' in toot and os.path.isfile("{0}/{1}".format(MEDIAPATH, toot['media'])):
      media_id = mastodon.media_post(media_file="{0}/{1}".format(MEDIAPATH, toot['media']))
    if 'schedule' in toot:
      schedule = datetime.datetime.now() + datetime.timedelta(minutes=toot['schedule'])

    mastodon.status_post(toot['text'], media_ids=media_id, scheduled_at=schedule, idempotency_key=idempotency)

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
    mastodon.account_update_credentials(avatar="{0}/{1}".format(MEDIAPATH, account['avatar']))
  if 'header' in account:
    mastodon.account_update_credentials(header="{0}/{1}".format(MEDIAPATH, account['header']))


def create_app(login, secret_file, scopes=DEFAULT_SCOPES):
  Mastodon.create_app(
    '{0}_bot'.format(login),
    api_base_url = API_URL,
    to_file = secret_file,
    scopes=scopes
  )

  return Mastodon(
    client_id=secret_file,
    api_base_url=API_URL,
    feature_set="pleroma"
  )

def register_user(login, email, password):
  secret_file = 'masto_bot/{0}.secret'.format(login)

  if os.path.isfile(secret_file):
    return login_user(login, email, password)

  mastodon = create_app(login, secret_file)
  mastodon.create_account(
      username=login,
      password=password,
      email=email,
      agreement=True,
      to_file=secret_file
  )
  print('user created')
  return mastodon

def login_user(login, email, password, scopes=DEFAULT_SCOPES):
  secret_file = 'masto_bot/{0}.secret'.format(login)
  mastodon = create_app(login, secret_file, scopes)

  mastodon.log_in(
    username=login,
    password=password,
    to_file=secret_file,
    scopes=scopes
  )
  print("login sucessful")
  return mastodon

def reset_user(admin_mastodon, nickname):
  try:
    admin_mastodon._Mastodon__api_request('DELETE', USERS_ENDPOINT, {'nickname': nickname})
  except:
    pass

def reset_toots(mastodon, uid):
  toots = mastodon.account_statuses(uid)
  for toot in toots:
    mastodon.status_delete(toot['id'])

@click.command()
@click.option('--mastobotconfig', default='/etc/mastobot/config.yaml', help='Mastobot config')
@click.option('--bootstrapconfig', default='/etc/mastobot/bootstrap.yaml', help='Mastobot Bootstrap config')
@click.option('--reset', default=False, envvar='MASTO_RESET', is_flag=True, help='Reset')
def main(mastobotconfig, bootstrapconfig, reset):
  global API_URL, MEDIAPATH

  with open(mastobotconfig) as f:
    mastobotconfig_dict = yaml.safe_load(f)

  MEDIAPATH = mastobotconfig_dict.get('mediapath', MEDIAPATH)
  API_URL = "https://{0}".format(mastobotconfig_dict['server_name'])

  with open(bootstrapconfig) as f:
    config_dict = yaml.safe_load(f)

  os.makedirs('masto_bot', exist_ok=True)

  admin_mastodon = login_user(
    config_dict['admin_user'].split('@')[0],
    config_dict['admin_user'],
    config_dict['admin_password'],
    scopes=ADMIN_SCOPES)

  existing_users = admin_mastodon._Mastodon__api_request('GET', USERS_ENDPOINT)
  
  nicknames = [ existing_user['nickname'] for existing_user in existing_users['users'] ]

  for user in config_dict['mastodon_users']:

    try:
      if reset:
        os.remove('masto_bot/{0}.secret'.format(user['login']))
        reset_toots(mastodon, mastodon.me()['id'])
        return

      if user['login'] in nicknames:
        print(f"*** trying to login as { user['login'] }***")
        mastodon = login_user(
          user['login'],
          user['email'],
          user.get('password', 'password'))
      else:
        print(f"*** trying to register as { user['login'] }***")
        mastodon = register_user(
          user['login'],
          user['email'],
          user.get('password', 'password')
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


if __name__ == '__main__':
  main() # pylint: disable=no-value-for-parameter

