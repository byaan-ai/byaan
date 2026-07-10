import secrets
from datetime import datetime

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.backend import auth_backend, get_jwt_strategy
from server.auth.config import current_active_user, fastapi_users
from server.auth.manager import UserManager, get_user_manager
from server.db.session import get_async_session
from server.models.tenant_invitation import InvitationStatus, TenantInvitation
from server.models.tenant_member import TenantMember
from server.models.user import User
from server.models.verification_token import VerificationToken
from server.schemas.auth import GoogleAuthRequest, RefreshTokenRequest, TokenPairResponse
from server.schemas.standard_response import success_response
from server.schemas.user import UserCreate, UserRead
from server.services.google_oauth_service import GoogleOAuthError, GoogleOAuthService
from server.services.refresh_token_service import RefreshTokenService
from server.utils.config_loader import is_self_hosted, should_hide_email_auth

router = APIRouter()

auth_router = fastapi_users.get_auth_router(auth_backend)


def _set_refresh_cookie(response: Response, request: Request, token: str) -> None:
    from server.utils.deployment import should_use_secure_cookie

    response.set_cookie(
        key="refresh_token",
        value=token,
        httponly=True,
        secure=should_use_secure_cookie(request),
        samesite="strict",
        max_age=RefreshTokenService.refresh_token_max_age_seconds(),
        path="/api/auth",
    )


def _set_csrf_cookie(response: Response, request: Request) -> None:
    from server.utils.deployment import should_use_secure_cookie

    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,
        secure=should_use_secure_cookie(request),
        samesite="strict",
        max_age=RefreshTokenService.refresh_token_max_age_seconds(),
        path="/",
    )


def _clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(key="csrf_token", path="/", samesite="strict")


@router.post("/auth/login", response_model=TokenPairResponse, tags=["auth"])
async def custom_login(
    request: Request,
    response: Response,
    credentials: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
):
    if should_hide_email_auth():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email/password authentication is disabled. Please use Google Sign-In.",
        )

    user = await user_manager.authenticate(credentials)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="LOGIN_BAD_CREDENTIALS")

    strategy = get_jwt_strategy()
    access_token = await strategy.write_token(user)

    ip = request.client.host if request.client else None
    refresh_token = await RefreshTokenService.create(user.id, ip, session)

    _set_refresh_cookie(response, request, refresh_token)

    if is_self_hosted():
        _set_csrf_cookie(response, request)

    return TokenPairResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/auth/google", response_model=TokenPairResponse, tags=["auth"])
async def google_auth(
    request: Request,
    response: Response,
    payload: GoogleAuthRequest,
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
):
    """
    Authenticate or register a user via Google OAuth.

    - Verifies the Google ID token
    - If user exists (by google_id or email), logs them in
    - If user doesn't exist, creates a new account
    - Returns access and refresh tokens
    """
    try:
        # Verify Google token and get user info
        google_user = await GoogleOAuthService.verify_google_token(payload.credential)
    except GoogleOAuthError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    email = google_user["email"]
    google_id = google_user["google_id"]

    # Try to find existing user by google_id first
    result = await session.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    if not user:
        # Try to find by email (user might have registered with email/password first)
        user = await user_manager.user_db.get_by_email(email)

        if user:
            # Link Google account to existing user
            user.google_id = google_id
            if google_user.get("picture") and not user.avatar_url:
                user.avatar_url = google_user["picture"]
            if google_user.get("name") and not user.full_name:
                user.full_name = google_user["name"]
            # Mark as verified since Google verified the email
            user.is_verified = True
            await session.commit()
            await session.refresh(user)
        else:
            # In self-hosted mode, new users must have a pending invitation
            pending_invitation = None
            if is_self_hosted():
                result = await session.execute(
                    select(TenantInvitation)
                    .where(TenantInvitation.email == email)
                    .where(TenantInvitation.status == InvitationStatus.PENDING.value)
                )
                pending_invitation = result.scalar_one_or_none()

                if not pending_invitation:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Registration is by invitation only. Please contact your administrator.",
                    )

            user = User(
                email=email,
                google_id=google_id,
                full_name=google_user.get("name"),
                avatar_url=google_user.get("picture"),
                hashed_password=user_manager.password_helper.hash(user_manager.password_helper.generate()),
                is_active=True,
                is_verified=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="USER_INACTIVE")

    # Auto-accept pending invitation in self-hosted mode (for both new and existing users)
    if is_self_hosted():
        result = await session.execute(
            select(TenantInvitation)
            .where(TenantInvitation.email == user.email)
            .where(TenantInvitation.status == InvitationStatus.PENDING.value)
        )
        pending_invitation = result.scalar_one_or_none()

        if pending_invitation:
            # Check if user is already a member of this tenant
            existing_member_result = await session.execute(
                select(TenantMember)
                .where(TenantMember.user_id == user.id)
                .where(TenantMember.tenant_id == pending_invitation.tenant_id)
            )
            existing_member = existing_member_result.scalar_one_or_none()

            if not existing_member:
                # Create tenant membership
                member = TenantMember(
                    user_id=user.id,
                    tenant_id=pending_invitation.tenant_id,
                    role=pending_invitation.role,
                    invited_at=pending_invitation.created_at,
                    joined_at=datetime.utcnow(),
                )
                session.add(member)

                # Mark invitation as accepted
                pending_invitation.status = InvitationStatus.ACCEPTED.value
                pending_invitation.accepted_at = datetime.utcnow()

                # Mark verification token as used
                if pending_invitation.token_id:
                    verification_token = await session.get(VerificationToken, pending_invitation.token_id)
                    if verification_token:
                        verification_token.verified_at = datetime.utcnow()

                await session.commit()

    # Generate tokens
    strategy = get_jwt_strategy()
    access_token = await strategy.write_token(user)

    ip = request.client.host if request.client else None
    refresh_token = await RefreshTokenService.create(user.id, ip, session)

    _set_refresh_cookie(response, request, refresh_token)

    if is_self_hosted():
        _set_csrf_cookie(response, request)

    return TokenPairResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/auth/refresh", response_model=TokenPairResponse, tags=["auth"])
async def refresh_token(
    request: Request,
    response: Response,
    payload: RefreshTokenRequest | None = None,
    refresh_token_cookie: str | None = Cookie(default=None, alias="refresh_token"),
    csrf_cookie: str | None = Cookie(default=None, alias="csrf_token"),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    session: AsyncSession = Depends(get_async_session),
):
    if is_self_hosted() and refresh_token_cookie and not (payload and payload.refresh_token):
        if not x_csrf_token or not csrf_cookie or x_csrf_token != csrf_cookie:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF_VALIDATION_FAILED")

    token_value = refresh_token_cookie or (payload.refresh_token if payload else None)
    if not token_value:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_REFRESH_TOKEN")

    token_record = await RefreshTokenService.verify(token_value, session)
    if not token_record:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_REFRESH_TOKEN")

    user = await session.get(User, token_record.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="USER_INACTIVE")

    strategy = get_jwt_strategy()
    access_token = await strategy.write_token(user)

    ip = request.client.host if request.client else None
    new_refresh_token = await RefreshTokenService.create(user.id, ip, session)

    _set_refresh_cookie(response, request, new_refresh_token)

    if is_self_hosted():
        _set_csrf_cookie(response, request)

    return TokenPairResponse(access_token=access_token, refresh_token=new_refresh_token)


@router.post("/auth/logout", tags=["auth"])
async def logout_session(
    request: Request,
    response: Response,
    refresh_token_cookie: str | None = Cookie(default=None, alias="refresh_token"),
    session: AsyncSession = Depends(get_async_session),
):
    from server.utils.deployment import should_use_secure_cookie

    if refresh_token_cookie:
        await RefreshTokenService.revoke_token(refresh_token_cookie, session)
        await session.commit()

    secure = should_use_secure_cookie(request)
    response.delete_cookie(key="refresh_token", path="/api/auth", secure=secure, httponly=True, samesite="strict")

    if is_self_hosted():
        _clear_csrf_cookie(response)

    return success_response(data=None, message="Logged out")


@router.post("/auth/logout-all", tags=["auth"])
async def logout_all_sessions(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await RefreshTokenService.revoke_all_for_user(user.id, session)
    await session.commit()
    return success_response(data=None, message="All sessions have been logged out")


for route in auth_router.routes:
    if hasattr(route, "path") and route.path == "/login":
        continue
    router.routes.append(route)


class RegisterWithInvitationRequest(BaseModel):
    """Request body for registration with invitation."""

    email: EmailStr
    password: str
    full_name: str
    invitation_token: str


@router.post(
    "/auth/register-with-invitation", response_model=UserRead, status_code=status.HTTP_201_CREATED, tags=["auth"]
)
async def register_with_invitation(
    request: Request,
    data: RegisterWithInvitationRequest,
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
):
    """
    Register a new user with an invitation token.

    - Validates the invitation token
    - Creates user with is_verified=True (no email verification needed)
    - Does NOT send verification email
    """
    # Verify invitation token
    token_hash_value = RefreshTokenService.hash_token(data.invitation_token)

    # Find verification token
    result = await session.execute(select(VerificationToken).where(VerificationToken.token_hash == token_hash_value))
    verification_token = result.scalar_one_or_none()

    if not verification_token or verification_token.expires_at < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invitation token",
        )

    if verification_token.verified_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitation link has already been used",
        )

    result = await session.execute(
        select(TenantInvitation)
        .where(TenantInvitation.token_id == verification_token.id)
        .where(TenantInvitation.email == data.email)
        .where(TenantInvitation.status == InvitationStatus.PENDING.value)
    )
    invitation = result.scalar_one_or_none()

    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending invitation found for this email",
        )

    # Check if user already exists
    existing_user = await user_manager.user_db.get_by_email(data.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="REGISTER_USER_ALREADY_EXISTS",
        )

    # Create user with is_verified=True
    user_create = UserCreate(
        email=data.email,
        password=data.password,
        full_name=data.full_name,
        is_verified=True,  # Skip email verification for invited users
    )

    # Create user without triggering email verification
    user_dict = user_create.model_dump()
    user_dict.pop("password", None)

    user = User(**user_dict)

    user.hashed_password = user_manager.password_helper.hash(data.password)
    user.is_verified = True
    user.is_active = True
    user.is_superuser = False

    session.add(user)
    await session.commit()
    await session.refresh(user)

    return UserRead.model_validate(user)


class SetPasswordWithInvitationRequest(BaseModel):
    invitation_token: str
    password: str


@router.post("/auth/set-password-with-invitation", tags=["auth"])
async def set_password_with_invitation(
    data: SetPasswordWithInvitationRequest,
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
):
    """Set password for an invited user when Google OAuth isn't configured.
    Disabled when Google OAuth is available — invitees must complete via Google."""

    from server.utils.config_loader import get_google_oauth_config

    if bool(get_google_oauth_config().get("client_id")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password setup is disabled. Please complete sign-in with Google.",
        )

    if len(data.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters",
        )

    token_hash_value = RefreshTokenService.hash_token(data.invitation_token)

    result = await session.execute(select(VerificationToken).where(VerificationToken.token_hash == token_hash_value))
    verification_token = result.scalar_one_or_none()

    if not verification_token or verification_token.expires_at < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invitation token",
        )

    if verification_token.verified_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitation link has already been used",
        )

    result = await session.execute(
        select(TenantInvitation)
        .where(TenantInvitation.token_id == verification_token.id)
        .where(TenantInvitation.status == InvitationStatus.PENDING.value)
    )
    invitation = result.scalar_one_or_none()

    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending invitation found",
        )

    user = await user_manager.user_db.get_by_email(invitation.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found for this invitation",
        )

    user.hashed_password = user_manager.password_helper.hash(data.password)
    await session.commit()

    return success_response(data=None, message="Password set successfully")


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None


if not is_self_hosted():

    @router.post("/auth/register", response_model=UserRead, status_code=status.HTTP_201_CREATED, tags=["auth"])
    async def register(
        data: RegisterRequest,
        session: AsyncSession = Depends(get_async_session),
        user_manager: UserManager = Depends(get_user_manager),
    ):
        existing_user = await user_manager.user_db.get_by_email(data.email)
        if existing_user:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="REGISTER_USER_ALREADY_EXISTS")
        user = User(
            email=data.email,
            full_name=data.full_name,
            hashed_password=user_manager.password_helper.hash(data.password),
            is_active=True,
            is_verified=True,
            is_superuser=False,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return UserRead.model_validate(user)

else:

    @router.post("/auth/register", status_code=status.HTTP_403_FORBIDDEN, tags=["auth"])
    async def register_disabled():
        """Registration is disabled in self-hosted mode. Use invitations."""
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Public registration is disabled. Please contact your administrator for an invitation.",
        )


router.include_router(
    fastapi_users.get_reset_password_router(),
    prefix="/auth",
    tags=["auth"],
)
