"""
email_service.py

Sends password reset verification codes via Gmail SMTP. Constructs both
plain-text and HTML email bodies with the six-digit code.

Author: Dominique Anne Lee, Anna Yabut
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_reset_code_email(to_email: str, code: str) -> None:
    """
    Sends a password reset verification code to a user via email.

    Constructs both plain-text and HTML email formats and delivers the message
    using Gmail SMTP with credentials from environment variables.

    Args:
        to_email: Recipient email address.
        code: Six-digit verification code to include in the email.

    Raises:
        ValueError: If SMTP credentials are not set in environment variables.
        smtplib.SMTPException: If sending the email fails due to SMTP issues.
    """
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

def send_staff_account_created_email(to_email: str, first_name: str = "") -> None:
    """
    Sends a new staff account creation email with instructions to use the
    Forgot Password flow to access the account.

    Args:
        to_email: Recipient email address.
        first_name: Optional first name for a friendlier greeting.

    Raises:
        ValueError: If SMTP credentials are not set in environment variables.
        smtplib.SMTPException: If sending the email fails due to SMTP issues.
    """
    smtp_email = os.getenv("SMTP_EMAIL")
    smtp_password = os.getenv("SMTP_APP_PASSWORD")
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")

    if not smtp_email or not smtp_password:
        raise ValueError("SMTP_EMAIL or SMTP_APP_PASSWORD is not set")

    greeting = f"Hello {first_name}," if first_name else "Hello,"
    forgot_password_url = f"{frontend_url}/login"

    message = MIMEMultipart("alternative")
    message["Subject"] = "Your GBTAC staff account has been created"
    message["From"] = smtp_email
    message["To"] = to_email

    html_content = f"""
    <html>
      <body>
        <p>{greeting}</p>
        <p>A staff account has been created for you in the GBTAC system.</p>
        <p>
          To access your account, please go to the login page and select
          <strong>Forgot Password</strong> using this email address.
        </p>
        <p>
          Login page:
          <a href="{forgot_password_url}">{forgot_password_url}</a>
        </p>
        <p>
          After completing the password reset process, you will be able to sign in.
        </p>
        <p>If you were not expecting this account, please contact the administrator.</p>
        <p>Thank you.</p>
      </body>
    </html>
    """

    text_content = f"""
    {greeting}

    A staff account has been created for you in the GBTAC system.

    To access your account, please go to the login page and select Forgot Password
    using this email address.

    Login page: {forgot_password_url}

    After completing the password reset process, you will be able to sign in.

    If you were not expecting this account, please contact the administrator.

    Thank you.
    """

    message.attach(MIMEText(text_content, "plain"))
    message.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(smtp_email, smtp_password)
        server.sendmail(smtp_email, to_email, message.as_string())