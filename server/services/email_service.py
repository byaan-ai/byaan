from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from server.templates.emails import (
    get_invitation_email_html,
    get_invitation_email_text,
    get_password_reset_email_html,
    get_password_reset_email_text,
    get_skill_digest_html,
    get_skill_digest_text,
    get_verification_email_html,
    get_verification_email_text,
)
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


class EmailService:
    def __init__(self, api_key: str, from_email: str):
        self._api_key = api_key
        self._from_email = from_email
        self._base_url = "https://api.resend.com"

    async def _send_email(self, to_email: str, subject: str, html: str, text: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/emails",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": f"Byaan <{self._from_email}>",
                    "to": [to_email],
                    "subject": subject,
                    "html": html,
                    "text": text,
                },
            )

            if response.status_code == 200:
                logger.info(f"Email sent successfully to {to_email}")
                return {"success": True, "data": response.json()}
            else:
                logger.error(f"Failed to send email to {to_email}: {response.text}")
                return {"success": False, "error": response.text}

    async def send_verification_email(
        self,
        to_email: str,
        verification_link: str,
        name: str | None = None,
    ) -> dict:
        html_content = get_verification_email_html(
            email=to_email,
            verification_link=verification_link,
            name=name,
        )
        text_content = get_verification_email_text(
            email=to_email,
            verification_link=verification_link,
            name=name,
        )
        return await self._send_email(
            to_email=to_email,
            subject="Verify Your Email - Byaan",
            html=html_content,
            text=text_content,
        )

    async def send_password_reset_email(
        self,
        to_email: str,
        reset_link: str,
        name: str | None = None,
    ) -> dict:
        html_content = get_password_reset_email_html(
            email=to_email,
            reset_link=reset_link,
            name=name,
        )
        text_content = get_password_reset_email_text(
            email=to_email,
            reset_link=reset_link,
            name=name,
        )
        return await self._send_email(
            to_email=to_email,
            subject="Reset Your Password - Byaan",
            html=html_content,
            text=text_content,
        )

    async def send_invitation_email(
        self,
        to_email: str,
        invitation_link: str,
        tenant_name: str,
        inviter_name: str,
        role: str,
        expiration_days: int = 7,
    ) -> dict:
        html_content = get_invitation_email_html(
            email=to_email,
            invitation_link=invitation_link,
            tenant_name=tenant_name,
            inviter_name=inviter_name,
            role=role,
            expiration_days=expiration_days,
        )
        text_content = get_invitation_email_text(
            email=to_email,
            invitation_link=invitation_link,
            tenant_name=tenant_name,
            inviter_name=inviter_name,
            role=role,
            expiration_days=expiration_days,
        )
        return await self._send_email(
            to_email=to_email,
            subject=f"You've been invited to join {tenant_name} on Byaan",
            html=html_content,
            text=text_content,
        )

    async def send_skill_digest_email(
        self,
        to_email: str,
        tenant_name: str,
        stats: dict,
        suggestions: list[dict],
        frontend_url: str,
    ) -> dict:
        html_content = get_skill_digest_html(
            tenant_name=tenant_name,
            stats=stats,
            suggestions=suggestions,
            frontend_url=frontend_url,
        )
        text_content = get_skill_digest_text(
            tenant_name=tenant_name,
            stats=stats,
            suggestions=suggestions,
            frontend_url=frontend_url,
        )
        return await self._send_email(
            to_email=to_email,
            subject=f"Skill Learning Digest - {tenant_name}",
            html=html_content,
            text=text_content,
        )


class SMTPEmailService:
    """Email service using SMTP for self-hosted deployments."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_username: str,
        smtp_password: str,
        smtp_from_email: str,
        smtp_from_name: str | None = None,
        smtp_use_tls: bool = True,
    ):
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_username = smtp_username
        self._smtp_password = smtp_password
        self._smtp_from_email = smtp_from_email
        self._smtp_from_name = smtp_from_name
        self._smtp_use_tls = smtp_use_tls

    async def _send_email(self, to_email: str, subject: str, html: str, text: str) -> dict:
        """Send email via SMTP."""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            if self._smtp_from_name:
                msg["From"] = f"{self._smtp_from_name} <{self._smtp_from_email}>"
            else:
                msg["From"] = self._smtp_from_email
            msg["To"] = to_email

            part_text = MIMEText(text, "plain")
            part_html = MIMEText(html, "html")
            msg.attach(part_text)
            msg.attach(part_html)

            if self._smtp_use_tls:
                server = smtplib.SMTP(self._smtp_host, self._smtp_port)
                server.ehlo()
                server.starttls()
                server.ehlo()
            else:
                server = smtplib.SMTP(self._smtp_host, self._smtp_port)

            if self._smtp_username and self._smtp_password:
                server.login(self._smtp_username, self._smtp_password)

            server.send_message(msg)
            server.quit()

            logger.info(f"Email sent successfully via SMTP to {to_email}")
            return {"success": True, "data": {"message": "Email sent via SMTP"}}

        except Exception as e:
            logger.error(f"Failed to send email via SMTP to {to_email}: {str(e)}")
            return {"success": False, "error": str(e)}

    async def send_verification_email(
        self,
        to_email: str,
        verification_link: str,
        name: str | None = None,
    ) -> dict:
        html_content = get_verification_email_html(
            email=to_email,
            verification_link=verification_link,
            name=name,
        )
        text_content = get_verification_email_text(
            email=to_email,
            verification_link=verification_link,
            name=name,
        )
        return await self._send_email(
            to_email=to_email,
            subject="Verify Your Email - Byaan",
            html=html_content,
            text=text_content,
        )

    async def send_password_reset_email(
        self,
        to_email: str,
        reset_link: str,
        name: str | None = None,
    ) -> dict:
        html_content = get_password_reset_email_html(
            email=to_email,
            reset_link=reset_link,
            name=name,
        )
        text_content = get_password_reset_email_text(
            email=to_email,
            reset_link=reset_link,
            name=name,
        )
        return await self._send_email(
            to_email=to_email,
            subject="Reset Your Password - Byaan",
            html=html_content,
            text=text_content,
        )

    async def send_invitation_email(
        self,
        to_email: str,
        invitation_link: str,
        tenant_name: str,
        inviter_name: str,
        role: str,
        expiration_days: int = 7,
    ) -> dict:
        html_content = get_invitation_email_html(
            email=to_email,
            invitation_link=invitation_link,
            tenant_name=tenant_name,
            inviter_name=inviter_name,
            role=role,
            expiration_days=expiration_days,
        )
        text_content = get_invitation_email_text(
            email=to_email,
            invitation_link=invitation_link,
            tenant_name=tenant_name,
            inviter_name=inviter_name,
            role=role,
            expiration_days=expiration_days,
        )
        return await self._send_email(
            to_email=to_email,
            subject=f"You've been invited to join {tenant_name} on Byaan",
            html=html_content,
            text=text_content,
        )

    async def send_skill_digest_email(
        self,
        to_email: str,
        tenant_name: str,
        stats: dict,
        suggestions: list[dict],
        frontend_url: str,
    ) -> dict:
        html_content = get_skill_digest_html(
            tenant_name=tenant_name,
            stats=stats,
            suggestions=suggestions,
            frontend_url=frontend_url,
        )
        text_content = get_skill_digest_text(
            tenant_name=tenant_name,
            stats=stats,
            suggestions=suggestions,
            frontend_url=frontend_url,
        )
        return await self._send_email(
            to_email=to_email,
            subject=f"Skill Learning Digest - {tenant_name}",
            html=html_content,
            text=text_content,
        )
