from django.urls import path

from plane.app.views.integration import (
    IntegrationViewSet,
    WorkspaceIntegrationViewSet,
    ImporterServiceListViewSet,
    GitHubCallbackView,
)

urlpatterns = [
    # App integrations list
    path(
        "integrations/",
        IntegrationViewSet.as_view(),
        name="integrations",
    ),
    # GitHub App callback (OAuth)
    path(
        "integrations/github/callback/",
        GitHubCallbackView.as_view(),
        name="github-callback",
    ),
    # Workspace integrations list
    path(
        "workspaces/<str:slug>/workspace-integrations/",
        WorkspaceIntegrationViewSet.as_view(),
        name="workspace-integrations",
    ),
    # Create workspace integration (after OAuth)
    path(
        "workspaces/<str:slug>/workspace-integrations/<str:provider>/",
        WorkspaceIntegrationViewSet.as_view(),
        name="workspace-integration-create",
    ),
    # Delete workspace integration
    path(
        "workspaces/<str:slug>/workspace-integrations/<uuid:pk>/provider/",
        WorkspaceIntegrationViewSet.as_view(),
        name="workspace-integration-delete",
    ),
    # Importers list
    path(
        "workspaces/<str:slug>/importers/",
        ImporterServiceListViewSet.as_view(),
        name="importers-list",
    ),
    # Delete importer
    path(
        "workspaces/<str:slug>/importers/<str:service>/<uuid:importer_id>/",
        ImporterServiceListViewSet.as_view(),
        name="importer-delete",
    ),
]
