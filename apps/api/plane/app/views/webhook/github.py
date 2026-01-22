# Python imports
import hashlib
import hmac
import json
import logging
import os

# Third party imports
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny

# Module imports
from plane.db.models import GithubRepositorySync
from plane.bgtasks.github_webhook_task import process_github_webhook
from plane.utils.exception_logger import log_exception


logger = logging.getLogger("plane.webhook")


class GithubWebhookEndpoint(APIView):
    """
    Endpoint for receiving GitHub webhook events.

    GitHub App sends webhook events to this endpoint when:
    - Issues are created, edited, closed, reopened, or deleted
    - Issue comments are created, edited, or deleted

    The endpoint verifies the webhook signature and delegates
    processing to a background task.
    """

    # No authentication required - GitHub webhooks use signature verification
    permission_classes = [AllowAny]
    authentication_classes = []

    def _verify_signature(self, payload_body: bytes, signature_header: str) -> bool:
        """
        Verify the GitHub webhook signature.

        GitHub signs webhook payloads using HMAC-SHA256 with the webhook secret.
        The signature is sent in the X-Hub-Signature-256 header.

        Args:
            payload_body: Raw request body bytes
            signature_header: Value of X-Hub-Signature-256 header

        Returns:
            True if signature is valid, False otherwise
        """
        webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET")

        if not webhook_secret:
            logger.warning("GITHUB_WEBHOOK_SECRET not configured")
            return False

        if not signature_header:
            return False

        # GitHub sends signature as "sha256=<hex_digest>"
        if not signature_header.startswith("sha256="):
            return False

        expected_signature = signature_header[7:]  # Remove "sha256=" prefix

        # Compute HMAC-SHA256 of payload using webhook secret
        computed_hmac = hmac.new(
            webhook_secret.encode("utf-8"),
            payload_body,
            hashlib.sha256
        )
        computed_signature = computed_hmac.hexdigest()

        # Use constant-time comparison to prevent timing attacks
        return hmac.compare_digest(computed_signature, expected_signature)

    def post(self, request):
        """
        Handle incoming GitHub webhook events.

        Headers:
            X-GitHub-Event: Event type (issues, issue_comment, etc.)
            X-Hub-Signature-256: HMAC-SHA256 signature for verification
            X-GitHub-Delivery: Unique delivery ID

        Body:
            JSON payload containing event data
        """
        # Get event type from header
        event_type = request.headers.get("X-GitHub-Event")
        delivery_id = request.headers.get("X-GitHub-Delivery")
        signature = request.headers.get("X-Hub-Signature-256")

        logger.info(f"Received GitHub webhook: event={event_type}, delivery={delivery_id}")

        # Verify webhook signature
        if not self._verify_signature(request.body, signature):
            logger.warning(f"Invalid webhook signature for delivery {delivery_id}")
            return Response(
                {"error": "Invalid signature"},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Parse payload
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return Response(
                {"error": "Invalid JSON payload"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Handle ping event (sent when webhook is first configured)
        if event_type == "ping":
            return Response({"message": "pong"}, status=status.HTTP_200_OK)

        # Only process issues and issue_comment events
        if event_type not in ["issues", "issue_comment"]:
            return Response(
                {"message": f"Event type '{event_type}' not supported"},
                status=status.HTTP_200_OK
            )

        # Get action from payload
        action = payload.get("action")

        # Extract repository information
        repository = payload.get("repository", {})
        repo_id = repository.get("id")

        if not repo_id:
            return Response(
                {"error": "Missing repository information"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Find the repository sync configuration
        try:
            repo_sync = GithubRepositorySync.objects.select_related(
                "repository", "workspace", "project", "actor"
            ).get(repository__repository_id=repo_id)
        except GithubRepositorySync.DoesNotExist:
            logger.info(f"No sync configured for repository {repo_id}")
            return Response(
                {"message": "Repository sync not configured"},
                status=status.HTTP_200_OK
            )

        # Delegate to background task
        process_github_webhook.delay(
            event_type=event_type,
            action=action,
            payload=payload,
            repository_sync_id=str(repo_sync.id),
        )

        logger.info(f"Queued webhook processing: event={event_type}, action={action}, repo_sync={repo_sync.id}")

        return Response(
            {"message": "Webhook received and queued for processing"},
            status=status.HTTP_202_ACCEPTED
        )
