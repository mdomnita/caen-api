#REPLACE WITH YOUR CREDENTIALS BASED ON PROXY PROVIDER and rename to proxies.py
proxy_list = [   
#     {'host': 'dc.oxylabs.io:8005', 'username': 'some_user', 'password': 'some_pass'},
 #    {'host': 'dc.oxylabs.io:8001', 'username': 'some_user', 'password': 'some_pass'},
  #   {'host': 'dc.oxylabs.io:8002', 'username': 'some_user', 'password': 'some_pass'},
   #  {'host': 'dc.oxylabs.io:8003', 'username': 'some_user', 'password': 'some_pass'},
    # {'host': 'dc.oxylabs.io:8004', 'username': 'some_user', 'password': 'some_pass'},
    # New public proxies (IP:PORT + shared user/pass)

    {'host': 'p.webshare.io:80/', 'username': 'some_user', 'password': 'some_pass'},
    {'host': 'p.webshare.io:80/', 'username': 'some_user', 'password': 'some_pass'},
    {'host': 'p.webshare.io:80/', 'username': 'some_user', 'password': 'some_pass'},
    {'host': 'p.webshare.io:80/', 'username': 'some_user', 'password': 'some_pass'},
    {'host': 'p.webshare.io:80/', 'username': 'some_user', 'password': 'some_pass'}
]

def build_proxy_url(proxy_dict):
    host = proxy_dict['host']
    username = proxy_dict['username']
    password = proxy_dict['password']

    # Detect if it's Oxylabs (or similar) by checking host
    if 'oxylabs.io' in host:
        country = proxy_dict.get('country', 'us')  # default fallback
        username_part = f"user-{username}-country-{country}"
        auth = f"{username_part}:{password}"
        return f"https://{auth}@{host}"
    else:
        # Standard proxy: just user:pass
        auth = f"{username}:{password}"
        return f"http://{auth}@{host}"

def get_requests_proxy(proxy_dict):
    proxy_url = build_proxy_url(proxy_dict)

    if 'oxylabs.io' in proxy_url:
        return {"http": proxy_url, "https": proxy_url}
    else:
        return {"http": proxy_url, "https": proxy_url}
