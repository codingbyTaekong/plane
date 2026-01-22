# Python imports
import os

# Third Party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.views.base import BaseAPIView
from plane.app.permissions import allow_permission, ROLE
from plane.app.serializers import ImporterSerializer
from plane.db.models import (
    Integration,
    WorkspaceIntegration,
    Workspace,
    Importer,
    APIToken,
    User,
)
from plane.utils.exception_logger import log_exception


class IntegrationSerializer:
    """Simple serializer for Integration model"""

    @staticmethod
    def serialize(integration):
        return {
            "id": str(integration.id),
            "title": integration.title,
            "provider": integration.provider,
            "network": integration.network,
            "description": integration.description,
            "author": integration.author,
            "webhook_url": integration.webhook_url,
            "webhook_secret": integration.webhook_secret,
            "redirect_url": integration.redirect_url,
            "metadata": integration.metadata,
            "verified": integration.verified,
            "avatar_url": integration.avatar_url,
            "created_at": integration.created_at.isoformat() if integration.created_at else None,
            "updated_at": integration.updated_at.isoformat() if integration.updated_at else None,
            "created_by": str(integration.created_by) if integration.created_by else None,
            "updated_by": str(integration.updated_by) if integration.updated_by else None,
        }


class WorkspaceIntegrationSerializer:
    """Simple serializer for WorkspaceIntegration model"""

    @staticmethod
    def serialize(workspace_integration):
        return {
            "id": str(workspace_integration.id),
            "workspace": str(workspace_integration.workspace_id),
            "actor": str(workspace_integration.actor_id),
            "integration": str(workspace_integration.integration_id),
            "integration_detail": IntegrationSerializer.serialize(workspace_integration.integration),
            "api_token": str(workspace_integration.api_token_id),
            "metadata": workspace_integration.metadata,
            "config": workspace_integration.config,
            "created_at": workspace_integration.created_at.isoformat() if workspace_integration.created_at else None,
            "updated_at": workspace_integration.updated_at.isoformat() if workspace_integration.updated_at else None,
            "created_by": str(workspace_integration.created_by_id) if workspace_integration.created_by_id else None,
            "updated_by": str(workspace_integration.updated_by_id) if workspace_integration.updated_by_id else None,
        }


class IntegrationViewSet(BaseAPIView):
    """
    Endpoint to list available app integrations.

    GET /api/integrations/
    """

    def get(self, request):
        # Get or create GitHub integration
        github_integration, created = Integration.objects.get_or_create(
            provider="github",
            defaults={
                "title": "GitHub",
                "description": {
                    "short": "Import issues from GitHub repositories",
                    "long": "Connect your GitHub account to import issues, labels, and collaborators from your repositories."
                },
                "author": "Plane",
                "network": 2,  # Public
                "verified": True,
                "avatar_url": "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png",
                "redirect_url": os.environ.get("GITHUB_REDIRECT_URL", ""),
            }
        )

        integrations = Integration.objects.all()
        data = [IntegrationSerializer.serialize(i) for i in integrations]
        return Response(data, status=status.HTTP_200_OK)


class WorkspaceIntegrationViewSet(BaseAPIView):
    """
    Endpoint to list and manage workspace integrations.

    GET /api/workspaces/<slug>/workspace-integrations/
    POST /api/workspaces/<slug>/workspace-integrations/<provider>/
    DELETE /api/workspaces/<slug>/workspace-integrations/<pk>/provider/
    """

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace_integrations = WorkspaceIntegration.objects.filter(
            workspace__slug=slug
        ).select_related("integration", "actor", "api_token")

        data = [WorkspaceIntegrationSerializer.serialize(wi) for wi in workspace_integrations]
        return Response(data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug, provider):
        """
        Create a workspace integration after OAuth callback.

        For GitHub, the request should include:
        {
            "installation_id": "12345",
            "access_token": "ghu_xxxx"
        }
        """
        try:
            workspace = Workspace.objects.get(slug=slug)
            integration = Integration.objects.get(provider=provider)

            # Check if integration already exists
            existing = WorkspaceIntegration.objects.filter(
                workspace=workspace,
                integration=integration
            ).first()

            if existing:
                # Update existing integration
                existing.metadata = {
                    **existing.metadata,
                    **request.data.get("metadata", {}),
                    "installation_id": request.data.get("installation_id"),
                    "access_token": request.data.get("access_token"),
                }
                existing.save()
                return Response(
                    WorkspaceIntegrationSerializer.serialize(existing),
                    status=status.HTTP_200_OK
                )

            # Create API token for the integration
            api_token = APIToken.objects.create(
                user=request.user,
                label=f"{provider.title()} Integration",
                workspace=workspace,
            )

            # Create or get bot user for the integration
            bot_user, _ = User.objects.get_or_create(
                email=f"{provider}-bot@plane.local",
                defaults={
                    "username": f"{provider}-bot",
                    "is_bot": True,
                }
            )

            # Create workspace integration
            workspace_integration = WorkspaceIntegration.objects.create(
                workspace=workspace,
                integration=integration,
                actor=bot_user,
                api_token=api_token,
                metadata={
                    "installation_id": request.data.get("installation_id"),
                    "access_token": request.data.get("access_token"),
                },
                created_by=request.user,
                updated_by=request.user,
            )

            return Response(
                WorkspaceIntegrationSerializer.serialize(workspace_integration),
                status=status.HTTP_201_CREATED
            )

        except Workspace.DoesNotExist:
            return Response(
                {"error": "Workspace not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": f"Integration '{provider}' not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            log_exception(e)
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def delete(self, request, slug, pk):
        """Delete a workspace integration"""
        try:
            workspace_integration = WorkspaceIntegration.objects.get(
                workspace__slug=slug,
                pk=pk
            )
            workspace_integration.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except WorkspaceIntegration.DoesNotExist:
            return Response(
                {"error": "Workspace integration not found"},
                status=status.HTTP_404_NOT_FOUND
            )


class ImporterServiceListViewSet(BaseAPIView):
    """
    Endpoint to list all importers for a workspace.

    GET /api/workspaces/<slug>/importers/
    DELETE /api/workspaces/<slug>/importers/<service>/<importer_id>/
    """

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        importers = Importer.objects.filter(
            workspace__slug=slug
        ).select_related(
            "initiated_by",
            "project",
            "workspace",
        ).order_by("-created_at")

        serializer = ImporterSerializer(importers, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def delete(self, request, slug, service, importer_id):
        """Delete an importer record"""
        try:
            importer = Importer.objects.get(
                workspace__slug=slug,
                service=service,
                pk=importer_id
            )
            importer.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Importer.DoesNotExist:
            return Response(
                {"error": "Importer not found"},
                status=status.HTTP_404_NOT_FOUND
            )
