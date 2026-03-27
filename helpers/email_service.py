import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_reset_code_email(to_email: str, code: str):
    smtp_email = os.getenv("SMTP_EMAIL")
    smtp_password = os.getenv("SMTP_APP_PASSWORD")

    if not smtp_email or not smtp_password:
        raise ValueError("SMTP_EMAIL or SMTP_APP_PASSWORD is not set")

    message = MIMEMultipart("alternative")
    message["Subject"] = "Your GBTAC password reset code"
    message["From"] = smtp_email
    message["To"] = to_email

    html_content = f"""
    <html>
      <body>
        <p>You requested to reset your password.</p>
        <p>Your verification code is:</p>
        <h2 style="letter-spacing: 4px;">{code}</h2>
        <p>This code will expire in 10 minutes.</p>
        <p>If you did not request this, you can ignore this email.</p>
      </body>
    </html>
    """

    text_content = f"""
    You requested to reset your password.

    Your verification code is: {code}

    This code will expire in 10 minutes.

    If you did not request this, you can ignore this email.
    """

    message.attach(MIMEText(text_content, "plain"))
    message.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(smtp_email, smtp_password)
        server.sendmail(smtp_email, to_email, message.as_string())