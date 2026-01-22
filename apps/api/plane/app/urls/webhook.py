from django.urls import path

from plane.app.views import (
    GithubWebhookEndpoint,
    WebhookEndpoint,
    WebhookLogsEndpoint,
    WebhookSecretRegenerateEndpoint,
)


urlpatterns = [
    # GitHub inbound webhook (receives events from GitHub)
    path("webhooks/github/", GithubWebhookEndpoint.as_view(), name="github-webhook"),
    # Workspace webhooks (outbound)
    path("workspaces/<str:slug>/webhooks/", WebhookEndpoint.as_view(), name="webhooks"),
    path(
        "workspaces/<str:slug>/webhooks/<uuid:pk>/",
        WebhookEndpoint.as_view(),
        name="webhooks",
    ),
    path(
        "workspaces/<str:slug>/webhooks/<uuid:pk>/regenerate/",
        WebhookSecretRegenerateEndpoint.as_view(),
        name="webhooks",
    ),
    path(
        "workspaces/<str:slug>/webhook-logs/<uuid:webhook_id>/",
        WebhookLogsEndpoint.as_view(),
        name="webhooks",
    ),
]
