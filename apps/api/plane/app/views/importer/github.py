# Third Party imports
from rest_framework import status
from rest_framework.response import Response

# Django imports
from django.db import transaction

# Module imports
from plane.app.views.base import BaseAPIView
from plane.app.permissions import allow_permission, ROLE
from plane.app.serializers import ImporterSerializer
from plane.db.models import (
    WorkspaceIntegration,
    Workspace,
    Project,
    Importer,
    APIToken,
    GithubRepository,
    GithubRepositorySync,
)
from plane.bgtasks.github_import_task import github_import_task
from plane.utils.importers.github import GitHubAPIClient, GitHubAPIError
from plane.utils.exception_logger import log_exception


class GithubRepositoriesEndpoint(BaseAPIView):
    """
    Endpoint to list GitHub repositories accessible to the workspace integration.

    GET /api/workspaces/<slug>/workspace-integrations/<integration_id>/github-repositories/
    """

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug, integration_id):
        page = int(request.GET.get("page", 1))
        per_page = int(request.GET.get("per_page", 30))

        # Get workspace integration
        workspace_integration = WorkspaceIntegration.objects.select_related(
            "integration"
        ).get(
            workspace__slug=slug,
            pk=integration_id,
            integration__provider="github",
        )

        # Get GitHub access token from metadata
        access_token = workspace_integration.metadata.get("access_token")
        if not access_token:
            return Response(
                {"error": "GitHub access token not found in workspace integration"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            github_client = GitHubAPIClient(access_token)
            repositories = github_client.get_user_repositories(page=page, per_page=per_page)

            # Format response for frontend
            return Response({
                "repositories": repositories,
                "total_count": len(repositories) if page == 1 else 0,  # Approximate count
            }, status=status.HTTP_200_OK)

        except GitHubAPIError as e:
            log_exception(e)
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class GithubRepoInfoEndpoint(BaseAPIView):
    """
    Endpoint to get GitHub repository information including issue count,
    labels count, and collaborators.

    GET /api/workspaces/<slug>/importers/github/?owner=<owner>&repo=<repo>
    """

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        owner = request.GET.get("owner")
        repo = request.GET.get("repo")

        if not owner or not repo:
            return Response(
                {"error": "Both 'owner' and 'repo' query parameters are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get workspace integration for GitHub
        workspace_integration = WorkspaceIntegration.objects.select_related(
            "integration"
        ).filter(
            workspace__slug=slug,
            integration__provider="github",
        ).first()

        if not workspace_integration:
            return Response(
                {"error": "GitHub integration not found for this workspace"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Get GitHub access token from metadata
        access_token = workspace_integration.metadata.get("access_token")
        if not access_token:
            return Response(
                {"error": "GitHub access token not found in workspace integration"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            github_client = GitHubAPIClient(access_token)
            repo_info = github_client.get_repository_info(owner, repo)

            return Response(repo_info, status=status.HTTP_200_OK)

        except GitHubAPIError as e:
            log_exception(e)
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class GithubImporterEndpoint(BaseAPIView):
    """
    Endpoint to list and manage GitHub importers for a workspace.

    GET /api/workspaces/<slug>/importers/services/github/
    """

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        importers = Importer.objects.filter(
            workspace__slug=slug,
            service="github",
        ).select_related(
            "initiated_by",
            "project",
            "workspace",
        ).order_by("-created_at")

        serializer = ImporterSerializer(importers, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class GithubImporterServiceEndpoint(BaseAPIView):
    """
    Endpoint to create a new GitHub import job.

    POST /api/workspaces/<slug>/projects/importers/github/

    Request body:
    {
        "metadata": {
            "owner": "github-username",
            "name": "repo-name",
            "repository_id": 123456,
            "url": "https://github.com/owner/repo"
        },
        "data": {
            "users": [
                {"username": "github-user", "import": "map"|"invite"|false, "email": "user@example.com"}
            ]
        },
        "config": {
            "sync": true|false
        },
        "project_id": "uuid"
    }
    """

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def post(self, request, slug):
        metadata = request.data.get("metadata", {})
        data = request.data.get("data", {})
        config = request.data.get("config", {})
        project_id = request.data.get("project_id")

        # Validate required fields
        if not metadata.get("owner") or not metadata.get("name"):
            return Response(
                {"error": "metadata.owner and metadata.name are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not project_id:
            return Response(
                {"error": "project_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get workspace
        workspace = Workspace.objects.get(slug=slug)

        # Get project and verify it belongs to the workspace
        project = Project.objects.get(pk=project_id, workspace=workspace)

        # Get workspace integration for GitHub
        workspace_integration = WorkspaceIntegration.objects.select_related(
            "integration"
        ).filter(
            workspace=workspace,
            integration__provider="github",
        ).first()

        if not workspace_integration:
            return Response(
                {"error": "GitHub integration not found for this workspace"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Get GitHub access token from metadata
        access_token = workspace_integration.metadata.get("access_token")
        if not access_token:
            return Response(
                {"error": "GitHub access token not found in workspace integration"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                # Create API token for the import task
                api_token = APIToken.objects.create(
                    user=request.user,
                    label=f"GitHub Import - {metadata.get('name')}",
                    workspace=workspace,
                )

                # Create Importer record
                importer = Importer.objects.create(
                    workspace=workspace,
                    project=project,
                    service="github",
                    status="queued",
                    initiated_by=request.user,
                    metadata=metadata,
                    config=config,
                    data=data,
                    token=api_token,
                    created_by=request.user,
                    updated_by=request.user,
                )

                # Create GithubRepository record
                github_repo, _ = GithubRepository.objects.get_or_create(
                    workspace=workspace,
                    project=project,
                    repository_id=metadata.get("repository_id"),
                    defaults={
                        "name": metadata.get("name"),
                        "url": metadata.get("url"),
                        "owner": metadata.get("owner"),
                        "config": {},
                        "created_by": request.user,
                        "updated_by": request.user,
                    }
                )

                # Create GithubRepositorySync if sync is enabled
                if config.get("sync", False):
                    GithubRepositorySync.objects.get_or_create(
                        workspace=workspace,
                        project=project,
                        repository=github_repo,
                        defaults={
                            "actor": workspace_integration.actor,
                            "workspace_integration": workspace_integration,
                            "credentials": {"access_token": access_token},
                            "created_by": request.user,
                            "updated_by": request.user,
                        }
                    )

                # Start background task
                github_import_task.delay(
                    importer_id=str(importer.id),
                    workspace_id=str(workspace.id),
                    project_id=str(project.id),
                    access_token=access_token,
                    metadata=metadata,
                    user_data=data.get("users", []),
                )

                serializer = ImporterSerializer(importer)
                return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Project.DoesNotExist:
            return Response(
                {"error": "Project not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            log_exception(e)
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
