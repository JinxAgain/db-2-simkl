import requests

def main():
    print("==================================================")
    print("             Simkl OAuth Token Helper             ")
    print("==================================================")
    print("Note: Please make sure your Simkl App's Redirect URI")
    print("is set to 'http://localhost' in Simkl Settings.\n")
    
    client_id = input("1. Enter your Simkl Client ID: ").strip()
    client_secret = input("2. Enter your Simkl Client Secret: ").strip()
    
    redirect_uri = "http://localhost"
    auth_url = f"https://simkl.com/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}"
    
    print("\n3. Open the following URL in your web browser to authorize the application:")
    print(f"\n   {auth_url}\n")
    print("   After authorizing, your browser will redirect to a blank or broken localhost page.")
    print("   Look at your browser's address bar and copy the code after '?code='.")
    print("   Example URL: http://localhost/?code=ABC123XYZ... -> copy 'ABC123XYZ...'")
    
    pin = input("\n4. Enter the code copied from the address bar: ").strip()
    
    token_url = "https://api.simkl.com/oauth/token"
    payload = {
        "code": pin,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }
    
    print("\nRequesting Access Token...")
    try:
        resp = requests.post(token_url, json=payload, headers={"Content-Type": "application/json"})
        if resp.status_code == 200:
            data = resp.json()
            access_token = data.get("access_token")
            print("\n================== SUCCESS ==================")
            print("Successfully obtained access token!")
            print(f"\nSIMKL_ACCESS_TOKEN:\n{access_token}")
            print("=============================================")
            print("\nCopy the token above and add it to your GitHub Repository Secrets.")
        else:
            print(f"\nFailed to obtain token (Status {resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"\nAn error occurred: {e}")

if __name__ == "__main__":
    main()
