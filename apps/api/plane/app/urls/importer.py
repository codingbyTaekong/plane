from django.urls import path

from plane.app.views.importer import (
    GithubRepositoriesEndpoint,
    GithubRepoInfoEndpoint,
    GithubImporterEndpoint,
    GithubImporterServiceEndpoint,
)

urlpatterns = [
    # GitHub repositories list
    path(
        "workspaces/<str:slug>/workspace-integrations/<uuid:integration_id>/github-repositories/",
        GithubRepositoriesEndpoint.as_view(),
        name="github-repositories",
    ),
    # GitHub repository info (issue count, labels, collaborators)
    path(
        "workspaces/<str:slug>/importers/github/",
        GithubRepoInfoEndpoint.as_view(),
        name="github-repo-info",
    ),
    # GitHub importers list
    path(
        "workspaces/<str:slug>/importers/services/github/",
        GithubImporterEndpoint.as_view(),
        name="github-importers",
    ),
    # Create GitHub import
    path(
        "workspaces/<str:slug>/projects/importers/github/",
        GithubImporterServiceEndpoint.as_view(),
        name="github-importer-service",
    ),
]
