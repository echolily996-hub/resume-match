"""Real HTTP auth checks. PostgreSQL acceptance only when run against Compose."""
import argparse
import copy
import http.cookiejar
import json
import os
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://web')
    parser.add_argument('--origin', default='http://localhost:8080')
    parser.add_argument('--json', action='store_true', help='Print identity checkpoint only')
    args = parser.parse_args()
    base = args.base_url.rstrip('/') + '/api/v1'

    def client(jar=None):
        jar = jar if jar is not None else http.cookiejar.CookieJar()
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar

    def call(opener, method, path, expected, body=None, csrf=None):
        headers = {'Origin': args.origin}
        if csrf:
            headers['X-CSRF-Token'] = csrf
        data = None if body is None else json.dumps(body).encode()
        if data:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
        try:
            response = opener.open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            payload = response.read()
            assert response.code == expected, f'{method} {path}: expected {expected}, got {response.code}'
        return json.loads(payload) if payload else None

    a, jar_a = client()
    b, _ = client()
    call(a, 'GET', '/health', 200)
    call(a, 'GET', '/auth/me', 401)
    call(a, 'POST', '/auth/login', 401, {'username': 'nonexistent-test-user', 'password': 'wrong'})
    user_a = os.environ.get('SEED_USER_A', 'demo_a')
    user_b = os.environ.get('SEED_USER_B', 'demo_b')
    aa = call(a, 'POST', '/auth/login', 200, {
        'username': user_a, 'password': os.environ.get('SEED_PASSWORD_A', 'Demo-A-2026!')})
    bb = call(b, 'POST', '/auth/login', 200, {
        'username': user_b, 'password': os.environ.get('SEED_PASSWORD_B', 'Demo-B-2026!')})
    assert aa['user']['username'] == user_a and bb['user']['username'] == user_b
    assert aa['user']['id'] != bb['user']['id']
    assert call(a, 'GET', '/auth/me', 200)['user'] == aa['user']
    assert call(b, 'GET', '/auth/me', 200)['user'] == bb['user']
    call(a, 'POST', '/auth/logout', 403)
    assert call(a, 'GET', '/auth/me', 200)['user'] == aa['user']
    old_jar = http.cookiejar.CookieJar()
    for cookie in jar_a:
        old_jar.set_cookie(copy.copy(cookie))
    old, _ = client(old_jar)
    call(a, 'POST', '/auth/logout', 204, csrf=aa['csrf_token'])
    call(a, 'GET', '/auth/me', 401)
    call(old, 'GET', '/auth/me', 401)
    assert call(b, 'GET', '/auth/me', 200)['user'] == bb['user']
    call(b, 'POST', '/auth/logout', 204, csrf=bb['csrf_token'])
    # Checkpoint excludes passwords, hashes, cookies and CSRF tokens.
    checkpoint = {aa['user']['username']: aa['user']['id'], bb['user']['username']: bb['user']['id']}
    if args.json:
        print(json.dumps(checkpoint, sort_keys=True))
    else:
        print('PASS: health, anonymous rejection, invalid login, two identities, me, CSRF, logout and old-cookie revocation')


if __name__ == '__main__':
    main()
