"""
One-time local helper to obtain a YouTube OAuth2 refresh token.

Run this ONCE on your own machine. It opens a browser for consent and prints a
refresh token, which you then store as the YOUTUBE_REFRESH_TOKEN GitHub secret.
It is not part of the daily pipeline and should never run in CI.

Usage:
    1. Download your OAuth2 "Desktop app" credentials from Google Cloud Console
       and save them next to this script as `client_secret.json`.
    2. Run:  python get_youtube_token.py
    3. Approve access in the browser window that opens.
    4. Copy the printed refresh token into your GitHub repo secrets.
"""

import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRET_FILE = "client_secret.json"


def main() -> int:
    """
    Run the interactive OAuth2 flow and print the refresh token.

    Output:  process exit code (0 on success, 1 if client_secret.json missing).
    """
    secret_path = Path(CLIENT_SECRET_FILE)
    if not secret_path.exists():
        print(
            f"ERROR: {CLIENT_SECRET_FILE} not found in the current directory.\n"
            "Download your OAuth2 Desktop-app credentials from Google Cloud "
            "Console and save them here first."
        )
        return 1

    # access_type=offline + prompt=consent guarantees a refresh_token is issued.
    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
    credentials = flow.run_local_server(
        port=0, access_type="offline", prompt="consent"
    )

    if not credentials.refresh_token:
        print(
            "ERROR: No refresh token returned. Revoke the app's access at "
            "https://myaccount.google.com/permissions and run this again."
        )
        return 1

    print("\n" + "=" * 60)
    print("SUCCESS — your YouTube refresh token is:\n")
    print(credentials.refresh_token)
    print("\nStore this as the GitHub secret:  YOUTUBE_REFRESH_TOKEN")
    print("Do NOT commit it to the repository.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
