# Ansible-Role: acr-ansible-pleroma

AIT-CyberRange:  Installs and configures pleroma. 


## Requirements

- Debian or Ubuntu 

## Role Variables

```yaml
# List with defined keys
key_list: []

```

## Example Playbook

```yaml
- hosts: all
  roles:
    - acr-ansible-pleroma
```

## Using the mastobot server

### Create a Post by user VaxxiStop38
```
curl -X POST https://y.social:5000/post -H "Content-Type: application/json" -d '{ "username": "VaxxiStop38", "text": "Hello WORLD! #PID13", "media": "Breakpoint_Logo.png" }'
```
### Reply to the post with the string 'PID13' in the content
```
curl -X POST https://y.social:5000/reply -H "Content-Type: application/json" -d '{ "username": "CERT_at", "text": "Reply to post 13", "post_identifier": "#PID13" }'
```

## License

GPL-3.0

## Author

- Lenhard Reuter