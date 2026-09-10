import imaplib
import email
from email.header import decode_header
from assistant.config import get_setting

def read_unread_emails(limit=5):
    """Connects to Gmail via IMAP and reads unread emails."""
    email_address = get_setting("email_address")
    app_password = get_setting("email_app_password")

    if not email_address or not app_password:
        return "Email credentials not configured. Please ask the user to add email_address and email_app_password to config.json."

    if app_password.startswith("secret://"):
        try:
            from assistant.control.store import ControlStore
            from assistant.control.secrets import SecretStore, load_key
            store = ControlStore()
            secrets = SecretStore(store, key=load_key())
            app_password = secrets.resolve(app_password, capability="email.imap")
        except Exception as e:
            return f"Could not resolve secret for email password: {e}"

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(email_address, app_password)
        mail.select("inbox")

        status, messages = mail.search(None, "UNSEEN")
        if status != "OK":
            return "Failed to search for emails."

        email_ids = messages[0].split()
        if not email_ids:
            return "You have no unread emails."

        # Grab the latest N emails
        latest_ids = email_ids[-limit:]
        
        results = []
        for e_id in latest_ids:
            status, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    # Decode subject
                    subject, encoding = decode_header(msg.get("Subject", "No Subject"))[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding if encoding else "utf-8")
                    
                    # Decode From
                    from_header, encoding = decode_header(msg.get("From", "Unknown"))[0]
                    if isinstance(from_header, bytes):
                        from_header = from_header.decode(encoding if encoding else "utf-8")
                        
                    results.append(f"From: {from_header}\nSubject: {subject}")
                    
        mail.logout()
        
        summary = "\n\n".join(results)
        return f"Found {len(latest_ids)} unread emails:\n\n{summary}"

    except Exception as e:
        return f"Error reading emails: {e}"

import smtplib
from email.message import EmailMessage

def draft_email(to_address, subject, body):
    """Drafts an email via Google Workspace Gmail."""
    try:
        from assistant.workspace.gmail import draft_email as ws_draft_email
        res = ws_draft_email(to_address, subject, body)
        if res.get("verified"):
            return f"Draft successfully created and verified: '{subject}' to {to_address}"
        return f"Draft created: {res.get('notice', '')}"
    except Exception as e:
        return f"Error drafting email: {e}"


def send_email(to_address, subject, body):
    """Sends an email via Google Workspace Gmail API or SMTP fallback."""
    # Check if Google Workspace is connected
    try:
        from assistant.workspace.auth import is_workspace_live
        if is_workspace_live():
            from assistant.workspace.gmail import send_email as ws_send_email
            res = ws_send_email(to_address, subject, body)
            if res.get("verified"):
                return f"Successfully sent and verified email to {to_address} (ID: {res.get('id')})"
            return f"Sent email to {to_address}"
    except Exception as ws_err:
        logger.debug("Workspace send skipped, falling back to SMTP: %s", ws_err)

    email_address = get_setting("email_address")
    app_password = get_setting("email_app_password")

    if not email_address or not app_password:
        # If no SMTP credentials, attempt workspace send (which handles demo mode)
        try:
            from assistant.workspace.gmail import send_email as ws_send_email
            res = ws_send_email(to_address, subject, body)
            return f"Email sent to {to_address}: {res.get('notice', '')}"
        except Exception as e:
            return f"Error: Email credentials not configured and workspace unavailable: {e}"

    if app_password.startswith("secret://"):
        try:
            from assistant.control.store import ControlStore
            from assistant.control.secrets import SecretStore, load_key
            store = ControlStore()
            secrets = SecretStore(store, key=load_key())
            app_password = secrets.resolve(app_password, capability="email.smtp")
        except Exception as e:
            return f"Could not resolve secret for email password: {e}"

    try:
        msg = EmailMessage()
        msg.set_content(body)
        msg["Subject"] = subject
        msg["From"] = email_address
        msg["To"] = to_address

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(email_address, app_password)
            server.send_message(msg)

        return f"Successfully sent email to {to_address}"
    except Exception as e:
        return f"Error sending email: {e}"
