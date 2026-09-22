import os
import time

import jwt
import requests

app_id = os.getenv("github_app_id")
installation_id = os.getenv("github_app_installation_id")
private_key = os.getenv("github_app_private_key")


if not all([app_id, installation_id, private_key]):
    raise ValueError("One or more required environment variables are missing")


now = int(time.time())
payload = {
    "iat": now,  # Issued at time
    "exp": now + (10 * 60),  # JWT expiration time (10 minutes)
    "iss": app_id,  # GitHub App ID
}

try:
    jwt_token = jwt.encode(payload, private_key, algorithm="RS256")
except Exception as e:
    raise ValueError(f"Error encoding JWT: {e}")

headers = {
    "Authorization": f"Bearer {jwt_token}",
    "Accept": "application/vnd.github+json",
}

url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"


try:
    response = requests.post(url, headers=headers)
    response.raise_for_status()  # Raise an error for bad status codes
except requests.exceptions.HTTPError as http_err:
    raise SystemExit(f"HTTP error occurred: {http_err}")  # Handle specific HTTP error
except Exception as err:
    raise SystemExit(f"Other error occurred: {err}")  # Handle other errors

# Extract and print the access token
installation_access_token = response.json().get("token")
if installation_access_token:
    print(installation_access_token)
else:
    raise ValueError("Access token not found in the response")
