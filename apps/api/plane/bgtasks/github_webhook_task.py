# Python imports
import logging
from typing import Dict, Any, Optional

# Third party imports
from celery import shared_task

# Django imports
from django.db import transaction

# Module imports
from plane.db.models import (
    Issue,
    IssueComment,
    State,
    GithubRepositorySync,
    GithubIssueSync,
    GithubCommentSync,
)
from plane.bgtasks.github_import_task import (
    convert_markdown_to_html,
    map_github_state_to_plane_state,
)
from plane.utils.exception_logger import log_exception


logger = logging.getLogger("plane.webhook")


def handle_issue_opened(
    payload: Dict[str, Any],
    repo_sync: GithubRepositorySync,
) -> Optional[Issue]:
    """
    Handle GitHub issue opened event.

    Creates a new Plane issue and GithubIssueSync record.

    Args:
        payload: GitHub webhook payload
        repo_sync: Repository sync configuration

    Returns:
        Created Issue or None on error
    """
    github_issue = payload.get("issue", {})
    issue_number = github_issue.get("number")
    issue_id = github_issue.get("id")
    title = github_issue.get("title", "")[:255]
    body = github_issue.get("body") or ""
    html_url = github_issue.get("html_url", "")
    state_str = github_issue.get("state", "open")

    workspace = repo_sync.workspace
    project = repo_sync.project
    actor = repo_sync.actor

    # Check if issue already exists (avoid duplicates)
    existing_sync = GithubIssueSync.objects.filter(
        repository_sync=repo_sync,
        github_issue_id=issue_id,
    ).first()

    if existing_sync:
        logger.info(f"Issue {issue_number} already synced, skipping creation")
        return existing_sync.issue

    # Map GitHub state to Plane state
    plane_state = map_github_state_to_plane_state(project, state_str)

    # Convert markdown to HTML
    description_html = convert_markdown_to_html(body)

    with transaction.atomic():
        # Create the Plane issue
        issue = Issue.objects.create(
            workspace=workspace,
            project=project,
            name=title,
            description_html=description_html,
            description_stripped=body[:10000] if body else "",
            state=plane_state,
            external_source="github",
            external_id=str(issue_number),
            created_by=actor,
            updated_by=actor,
        )

        # Create sync record
        GithubIssueSync.objects.create(
            workspace=workspace,
            project=project,
            repository_sync=repo_sync,
            issue=issue,
            repo_issue_id=issue_number,
            github_issue_id=issue_id,
            issue_url=html_url,
            created_by=actor,
            updated_by=actor,
        )

        logger.info(f"Created Plane issue {issue.id} from GitHub issue {issue_number}")

    return issue


def handle_issue_edited(
    payload: Dict[str, Any],
    repo_sync: GithubRepositorySync,
) -> Optional[Issue]:
    """
    Handle GitHub issue edited event.

    Updates the corresponding Plane issue.

    Args:
        payload: GitHub webhook payload
        repo_sync: Repository sync configuration

    Returns:
        Updated Issue or None if not found
    """
    github_issue = payload.get("issue", {})
    issue_id = github_issue.get("id")
    title = github_issue.get("title", "")[:255]
    body = github_issue.get("body") or ""

    # Find the synced issue
    issue_sync = GithubIssueSync.objects.filter(
        repository_sync=repo_sync,
        github_issue_id=issue_id,
    ).select_related("issue").first()

    if not issue_sync:
        logger.warning(f"No sync found for GitHub issue {issue_id}")
        return None

    issue = issue_sync.issue
    description_html = convert_markdown_to_html(body)

    # Update the Plane issue
    issue.name = title
    issue.description_html = description_html
    issue.description_stripped = body[:10000] if body else ""
    issue.updated_by = repo_sync.actor
    issue.save(update_fields=[
        "name", "description_html", "description_stripped", "updated_by", "updated_at"
    ])

    logger.info(f"Updated Plane issue {issue.id} from GitHub issue edit")

    return issue


def handle_issue_closed(
    payload: Dict[str, Any],
    repo_sync: GithubRepositorySync,
) -> Optional[Issue]:
    """
    Handle GitHub issue closed event.

    Updates the Plane issue state to completed/cancelled.

    Args:
        payload: GitHub webhook payload
        repo_sync: Repository sync configuration

    Returns:
        Updated Issue or None if not found
    """
    github_issue = payload.get("issue", {})
    issue_id = github_issue.get("id")

    # Find the synced issue
    issue_sync = GithubIssueSync.objects.filter(
        repository_sync=repo_sync,
        github_issue_id=issue_id,
    ).select_related("issue").first()

    if not issue_sync:
        logger.warning(f"No sync found for GitHub issue {issue_id}")
        return None

    issue = issue_sync.issue
    project = repo_sync.project

    # Find a closed state
    closed_state = State.objects.filter(
        project=project,
        group__in=["completed", "cancelled"]
    ).order_by("sequence").first()

    if closed_state:
        issue.state = closed_state
        issue.updated_by = repo_sync.actor
        issue.save(update_fields=["state", "updated_by", "updated_at"])
        logger.info(f"Closed Plane issue {issue.id}")

    return issue


def handle_issue_reopened(
    payload: Dict[str, Any],
    repo_sync: GithubRepositorySync,
) -> Optional[Issue]:
    """
    Handle GitHub issue reopened event.

    Updates the Plane issue state back to open.

    Args:
        payload: GitHub webhook payload
        repo_sync: Repository sync configuration

    Returns:
        Updated Issue or None if not found
    """
    github_issue = payload.get("issue", {})
    issue_id = github_issue.get("id")

    # Find the synced issue
    issue_sync = GithubIssueSync.objects.filter(
        repository_sync=repo_sync,
        github_issue_id=issue_id,
    ).select_related("issue").first()

    if not issue_sync:
        logger.warning(f"No sync found for GitHub issue {issue_id}")
        return None

    issue = issue_sync.issue
    project = repo_sync.project

    # Find an open state
    open_state = State.objects.filter(
        project=project,
        group__in=["unstarted", "backlog"]
    ).order_by("sequence").first()

    if open_state:
        issue.state = open_state
        issue.updated_by = repo_sync.actor
        issue.save(update_fields=["state", "updated_by", "updated_at"])
        logger.info(f"Reopened Plane issue {issue.id}")

    return issue


def handle_issue_deleted(
    payload: Dict[str, Any],
    repo_sync: GithubRepositorySync,
) -> bool:
    """
    Handle GitHub issue deleted event.

    Deletes the corresponding Plane issue and sync record.

    Args:
        payload: GitHub webhook payload
        repo_sync: Repository sync configuration

    Returns:
        True if deleted, False otherwise
    """
    github_issue = payload.get("issue", {})
    issue_id = github_issue.get("id")

    # Find the synced issue
    issue_sync = GithubIssueSync.objects.filter(
        repository_sync=repo_sync,
        github_issue_id=issue_id,
    ).select_related("issue").first()

    if not issue_sync:
        logger.warning(f"No sync found for GitHub issue {issue_id}")
        return False

    issue = issue_sync.issue

    with transaction.atomic():
        # Delete sync record first (foreign key constraint)
        issue_sync.delete()
        # Delete the issue
        issue.delete()

    logger.info(f"Deleted Plane issue for GitHub issue {issue_id}")

    return True


def handle_comment_created(
    payload: Dict[str, Any],
    repo_sync: GithubRepositorySync,
) -> Optional[IssueComment]:
    """
    Handle GitHub issue comment created event.

    Creates a new Plane comment and GithubCommentSync record.

    Args:
        payload: GitHub webhook payload
        repo_sync: Repository sync configuration

    Returns:
        Created IssueComment or None on error
    """
    github_comment = payload.get("comment", {})
    github_issue = payload.get("issue", {})

    comment_id = github_comment.get("id")
    comment_body = github_comment.get("body") or ""
    issue_id = github_issue.get("id")

    workspace = repo_sync.workspace
    project = repo_sync.project
    actor = repo_sync.actor

    # Find the synced issue
    issue_sync = GithubIssueSync.objects.filter(
        repository_sync=repo_sync,
        github_issue_id=issue_id,
    ).select_related("issue").first()

    if not issue_sync:
        logger.warning(f"No issue sync found for GitHub issue {issue_id}")
        return None

    # Check if comment already exists
    existing_sync = GithubCommentSync.objects.filter(
        issue_sync=issue_sync,
        repo_comment_id=comment_id,
    ).first()

    if existing_sync:
        logger.info(f"Comment {comment_id} already synced, skipping creation")
        return existing_sync.comment

    comment_html = convert_markdown_to_html(comment_body)

    with transaction.atomic():
        # Create the Plane comment
        issue_comment = IssueComment.objects.create(
            workspace=workspace,
            project=project,
            issue=issue_sync.issue,
            comment_html=comment_html,
            external_source="github",
            external_id=str(comment_id),
            actor=actor,
            created_by=actor,
            updated_by=actor,
        )

        # Create sync record
        GithubCommentSync.objects.create(
            workspace=workspace,
            project=project,
            issue_sync=issue_sync,
            comment=issue_comment,
            repo_comment_id=comment_id,
            created_by=actor,
            updated_by=actor,
        )

        logger.info(f"Created Plane comment {issue_comment.id} from GitHub comment {comment_id}")

    return issue_comment


def handle_comment_edited(
    payload: Dict[str, Any],
    repo_sync: GithubRepositorySync,
) -> Optional[IssueComment]:
    """
    Handle GitHub issue comment edited event.

    Updates the corresponding Plane comment.

    Args:
        payload: GitHub webhook payload
        repo_sync: Repository sync configuration

    Returns:
        Updated IssueComment or None if not found
    """
    github_comment = payload.get("comment", {})
    github_issue = payload.get("issue", {})

    comment_id = github_comment.get("id")
    comment_body = github_comment.get("body") or ""
    issue_id = github_issue.get("id")

    # Find the issue sync
    issue_sync = GithubIssueSync.objects.filter(
        repository_sync=repo_sync,
        github_issue_id=issue_id,
    ).first()

    if not issue_sync:
        logger.warning(f"No issue sync found for GitHub issue {issue_id}")
        return None

    # Find the comment sync
    comment_sync = GithubCommentSync.objects.filter(
        issue_sync=issue_sync,
        repo_comment_id=comment_id,
    ).select_related("comment").first()

    if not comment_sync:
        logger.warning(f"No comment sync found for GitHub comment {comment_id}")
        return None

    comment = comment_sync.comment
    comment_html = convert_markdown_to_html(comment_body)

    # Update the comment
    comment.comment_html = comment_html
    comment.updated_by = repo_sync.actor
    comment.save(update_fields=["comment_html", "updated_by", "updated_at"])

    logger.info(f"Updated Plane comment {comment.id} from GitHub comment edit")

    return comment


def handle_comment_deleted(
    payload: Dict[str, Any],
    repo_sync: GithubRepositorySync,
) -> bool:
    """
    Handle GitHub issue comment deleted event.

    Deletes the corresponding Plane comment and sync record.

    Args:
        payload: GitHub webhook payload
        repo_sync: Repository sync configuration

    Returns:
        True if deleted, False otherwise
    """
    github_comment = payload.get("comment", {})
    github_issue = payload.get("issue", {})

    comment_id = github_comment.get("id")
    issue_id = github_issue.get("id")

    # Find the issue sync
    issue_sync = GithubIssueSync.objects.filter(
        repository_sync=repo_sync,
        github_issue_id=issue_id,
    ).first()

    if not issue_sync:
        logger.warning(f"No issue sync found for GitHub issue {issue_id}")
        return False

    # Find the comment sync
    comment_sync = GithubCommentSync.objects.filter(
        issue_sync=issue_sync,
        repo_comment_id=comment_id,
    ).select_related("comment").first()

    if not comment_sync:
        logger.warning(f"No comment sync found for GitHub comment {comment_id}")
        return False

    comment = comment_sync.comment

    with transaction.atomic():
        # Delete sync record first
        comment_sync.delete()
        # Delete the comment
        comment.delete()

    logger.info(f"Deleted Plane comment for GitHub comment {comment_id}")

    return True


@shared_task
def process_github_webhook(
    event_type: str,
    action: str,
    payload: Dict[str, Any],
    repository_sync_id: str,
):
    """
    Process GitHub webhook events.

    This task is called by the GithubWebhookEndpoint after signature verification.
    It handles issue and comment events, creating/updating/deleting Plane entities.

    Args:
        event_type: GitHub event type (issues, issue_comment)
        action: Event action (opened, edited, closed, etc.)
        payload: Full webhook payload
        repository_sync_id: UUID of GithubRepositorySync
    """
    try:
        # Get repository sync
        repo_sync = GithubRepositorySync.objects.select_related(
            "repository", "workspace", "project", "actor"
        ).get(pk=repository_sync_id)

        logger.info(
            f"Processing GitHub webhook: event={event_type}, action={action}, "
            f"repo={repo_sync.repository.name}"
        )

        # Handle issue events
        if event_type == "issues":
            if action == "opened":
                handle_issue_opened(payload, repo_sync)
            elif action == "edited":
                handle_issue_edited(payload, repo_sync)
            elif action == "closed":
                handle_issue_closed(payload, repo_sync)
            elif action == "reopened":
                handle_issue_reopened(payload, repo_sync)
            elif action == "deleted":
                handle_issue_deleted(payload, repo_sync)
            else:
                logger.info(f"Unhandled issue action: {action}")

        # Handle issue comment events
        elif event_type == "issue_comment":
            if action == "created":
                handle_comment_created(payload, repo_sync)
            elif action == "edited":
                handle_comment_edited(payload, repo_sync)
            elif action == "deleted":
                handle_comment_deleted(payload, repo_sync)
            else:
                logger.info(f"Unhandled comment action: {action}")

    except GithubRepositorySync.DoesNotExist:
        logger.error(f"Repository sync not found: {repository_sync_id}")
    except Exception as e:
        log_exception(e)
        logger.error(f"Error processing GitHub webhook: {e}")
        raise  # Re-raise to trigger Celery retry
