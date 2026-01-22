# Python imports
import os
import requests

# Third Party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.views.base import BaseAPIView
from plane.db.models import (
    Integration,
    WorkspaceIntegration,
    Workspace,
    APIToken,
    User,
)
from plane.utils.exception_logger import log_exception


class GitHubCallbackView(BaseAPIView):
    """
    Handle GitHub App installation callback.

    POST /api/integrations/github/callback/
    Body:
        - installation_id: GitHub App installation ID
        - code: Authorization code for OAuth (optional)
        - workspace_slug: Workspace slug
    """

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        installation_id = request.data.get("installation_id")
        code = request.data.get("code")
        workspace_slug = request.data.get("workspace_slug")

        # Validate required parameters
        if not installation_id:
            return Response(
                {"error": "Missing installation_id parameter"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not workspace_slug:
            return Response(
                {"error": "Missing workspace_slug parameter"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Get workspace
            workspace = Workspace.objects.get(slug=workspace_slug)
        except Workspace.DoesNotExist:
            return Response(
                {"error": f"Workspace not found: {workspace_slug}"},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            # Get or create GitHub integration
            integration, _ = Integration.objects.get_or_create(
                provider="github",
                defaults={
                    "title": "GitHub",
                    "description": {
                        "short": "Import issues from GitHub repositories",
                        "long": "Connect your GitHub account to import issues, labels, and collaborators from your repositories.",
                    },
                    "author": "Plane",
                    "network": 2,
                    "verified": True,
                    "avatar_url": "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png",
                },
            )

            # Exchange code for access token if code is provided
            access_token = None
            if code:
                access_token = self._exchange_code_for_token(code)

            # Check if workspace integration already exists
            existing_integration = WorkspaceIntegration.objects.filter(
                workspace=workspace, integration=integration
            ).first()

            if existing_integration:
                # Update existing integration
                existing_integration.metadata = {
                    **existing_integration.metadata,
                    "installation_id": installation_id,
                }
                if access_token:
                    existing_integration.metadata["access_token"] = access_token
                existing_integration.save()

                return Response(
                    {
                        "status": "success",
                        "message": "GitHub integration updated",
                        "workspace_slug": workspace_slug,
                    },
                    status=status.HTTP_200_OK,
                )
            else:
                # Create bot user for the integration
                bot_user, _ = User.objects.get_or_create(
                    email="github-bot@plane.local",
                    defaults={
                        "username": "github-bot",
                        "is_bot": True,
                    },
                )

                # Create API token for the integration
                api_token = APIToken.objects.create(
                    user=bot_user,
                    label="GitHub Integration",
                    workspace=workspace,
                )

                # Create workspace integration
                WorkspaceIntegration.objects.create(
                    workspace=workspace,
                    integration=integration,
                    actor=bot_user,
                    api_token=api_token,
                    metadata={
                        "installation_id": installation_id,
                        "access_token": access_token,
                    },
                )

                return Response(
                    {
                        "status": "success",
                        "message": "GitHub integration created",
                        "workspace_slug": workspace_slug,
                    },
                    status=status.HTTP_201_CREATED,
                )

        except Exception as e:
            log_exception(e)
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _exchange_code_for_token(self, code: str) -> str | None:
        """Exchange the authorization code for an access token."""
        client_id = os.environ.get("GITHUB_CLIENT_ID")
        client_secret = os.environ.get("GITHUB_CLIENT_SECRET")

        if not client_id or not client_secret:
            return None

        try:
            response = requests.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                },
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("access_token")

            return None

        except Exception as e:
            log_exception(e)
            return None
