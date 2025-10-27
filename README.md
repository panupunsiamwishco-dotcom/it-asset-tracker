
# IT Asset Tracker (Google Sheets + Login + Mobile QR)

## วิธีใช้ (Streamlit Cloud)
1) สร้าง Google Sheet เปล่า แล้วคัดลอกค่า ID (ส่วนกลางของ URL)
2) แชร์สิทธิ์ให้ service account (client_email) แบบ Editor
3) ไปที่ Streamlit → Settings → Secrets ใส่ค่า:
```
SHEET_ID = "1xxxxxxxxxxxxxxxxxxxxxxxxxxxx"

[gcp]
type = "service_account"
project_id = "your-project"
private_key_id = "xxxx"
private_key = "-----BEGIN PRIVATE KEY-----\n....\n-----END PRIVATE KEY-----\n"
client_email = "your-sa@your-project.iam.gserviceaccount.com"
client_id = "1234567890"
token_uri = "https://oauth2.googleapis.com/token"

[auth]
cookie_name = "it_asset_app"
cookie_key = "change_this_key"
  [auth.credentials.usernames.admin]
  email = "admin@example.com"
  name  = "Admin"
  password = "$2b$12$qyB8S7y2cH1oDk7kqRr0xO2cWWZgH1eXG9dM9n1x1hJj9m6eY1gk2"
```
4) requirements.txt ต้องมี:
```
streamlit-authenticator
gspread
google-auth
streamlit-qr-code-scanner
```
5) Deploy แล้วใช้งานได้เลย
