from curl_cffi import requests
import json

# ==============================================================================
# PASTE YOUR FRESH, NEW COPIED BROWSER COOKIE STRING AND UNIQUE CSRF KEY HERE
# ==============================================================================
RAW_COOKIE_STRING = "AQEDAS9ON0cAVCx0AAABoEEoX64AAAGgZTTjrk4AVy62msXDnplujM0qYHUHfUSC0D3LU7I5FXbNK-SEJpqRsjiymrdEZJrKWfJ50tHAFL3jiSAbFqqRrPEUWfl7yez91VPW9rgE7dU2bXy1JH-XdQLO"
JSESSIONID_TOKEN = "ajax:8399673192947890215"

# CLEAN ALTERNATIVE ENDPOINT: Targets identity dash profiles directly by public ID string
# Replace 'suyash-degaonkar' with the vanity URL handle of the profile you are viewing
PUBLIC_PROFILE_HANDLE = "suyash-degaonkar" 

url = f"https://linkedin.com/in/{PUBLIC_PROFILE_HANDLE}"

headers = {
    "accept": "application/vnd.linkedin.normalized+json+2.2",
    "accept-language": "en-US,en;q=0.9",
    "csrf-token": JSESSIONID_TOKEN,
    "x-restli-protocol-version": "2.0.0",
    "x-li-lang": "en_US",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "cookie": RAW_COOKIE_STRING
}

print(f"Dispatching clean profile context request to handle: {PUBLIC_PROFILE_HANDLE}...")
response = requests.get(url, headers=headers, impersonate="chrome")

print(f"Server Routing Status Code: {response.status_code}")

if "reCAPTCHA" in response.text or "challenge" in response.url:
    print("\n❌ Failed: Still caught by reCAPTCHA. LinkedIn has flagged your local IP address.")
    print("👉 Action: Disconnect any active VPNs, or switch your terminal network (e.g., to a mobile hotspot) to get a clean IP.")
elif "d_homepage-guest-home" in response.text:
    print("\n❌ Failed: Redirected to Guest Homepage. The cookies did not authenticate successfully.")
else:
    try:
        data = response.json()
        print("\n✅ Success! Data matrix parsed cleanly.")
        with open("profile_output.json", "w") as f:
            json.dump(data, f, indent=2)
        print("Data successfully written to local repository file: profile_output.json")
    except Exception as err:
        print(f"\n❌ JSON Generation Error: {err}")
        print("Raw payload dump snippet layout:")
        print(response.text[:600])
