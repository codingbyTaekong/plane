# Python imports
import re
import markdown
from typing import Dict, List, Any, Optional
from uuid import UUID

# Third party imports
from celery import shared_task

# Django imports
from django.db import transaction
from django.utils import timezone

# Module imports
from plane.db.models import (
    Importer,
    Issue,
    Label,
    State,
    User,
    Project,
    Workspace,
    WorkspaceMember,
    IssueAssignee,
    IssueLabel,
    IssueComment,
    GithubIssueSync,
    GithubRepositorySync,
    GithubCommentSync,
)
from plane.utils.importers.github import GitHubAPIClient, GitHubAPIError
from plane.utils.exception_logger import log_exception


def convert_markdown_to_html(markdown_text: Optional[str]) -> str:
    """Convert GitHub markdown to HTML."""
    if not markdown_text:
        return "<p></p>"

    try:
        html = markdown.markdown(
            markdown_text,
            extensions=["fenced_code", "tables", "nl2br"]
        )
        return html
    except Exception:
        return f"<p>{markdown_text}</p>"


def get_or_create_label(
    workspace: Workspace,
    project: Project,
    label_data: Dict,
    user: User
) -> Label:
    """Get or create a label from GitHub label data."""
    name = label_data.get("name", "")
    color = label_data.get("color", "")

    # Add # prefix to color if not present
    if color and not color.startswith("#"):
        color = f"#{color}"

    label, created = Label.objects.get_or_create(
        workspace=workspace,
        project=project,
        name=name,
        defaults={
            "color": color or "#000000",
            "created_by": user,
            "updated_by": user,
        }
    )

    return label


def map_github_state_to_plane_state(
    project: Project,
    github_state: str
) -> Optional[State]:
    """
    Map GitHub issue state to Plane state.

    GitHub states: "open", "closed"
    """
    if github_state == "open":
        # Try to find a state in the "unstarted" or "backlog" group
        state = State.objects.filter(
            project=project,
            group__in=["unstarted", "backlog"]
        ).order_by("sequence").first()
    else:
        # For closed issues, find a "completed" or "cancelled" state
        state = State.objects.filter(
            project=project,
            group__in=["completed", "cancelled"]
        ).order_by("sequence").first()

    # Fallback to default state
    if not state:
        state = State.objects.filter(project=project, default=True).first()

    # Last resort: any state
    if not state:
        state = State.objects.filter(project=project).first()

    return state


def find_user_by_mapping(
    workspace: Workspace,
    github_username: str,
    user_mappings: List[Dict]
) -> Optional[User]:
    """
    Find Plane user based on user mapping configuration.

    User mapping format:
    {"username": "github-user", "import": "map"|"invite"|false, "email": "user@example.com"}
    """
    for mapping in user_mappings:
        if mapping.get("username") == github_username:
            import_type = mapping.get("import")

            if import_type == "map":
                # Find user by email in workspace members
                email = mapping.get("email", "")
                if email:
                    workspace_member = WorkspaceMember.objects.filter(
                        workspace=workspace,
                        member__email=email,
                        is_active=True
                    ).select_related("member").first()

                    if workspace_member:
                        return workspace_member.member

            elif import_type == "invite":
                # For invite, we would need to handle invitation
                # For now, skip assignment
                pass

            elif import_type is False:
                # Don't import this user
                return None

    return None


@shared_task
def github_import_task(
    importer_id: str,
    workspace_id: str,
    project_id: str,
    access_token: str,
    metadata: Dict[str, Any],
    user_data: List[Dict],
):
    """
    Background task to import GitHub issues into Plane.

    Args:
        importer_id: UUID of the Importer record
        workspace_id: UUID of the workspace
        project_id: UUID of the project
        access_token: GitHub access token
        metadata: Repository metadata (owner, name, repository_id, url)
        user_data: User mapping data
    """
    try:
        # Get importer and update status
        importer = Importer.objects.get(pk=importer_id)
        importer.status = "processing"
        importer.save(update_fields=["status"])

        workspace = Workspace.objects.get(pk=workspace_id)
        project = Project.objects.get(pk=project_id)
        initiated_by = importer.initiated_by

        owner = metadata.get("owner")
        repo_name = metadata.get("name")

        # Initialize GitHub client
        github_client = GitHubAPIClient(access_token)

        # Fetch issues from GitHub
        github_issues = github_client.get_repository_issues(
            owner=owner,
            repo=repo_name,
            state="all",
            max_pages=20  # Limit to prevent very long imports
        )

        # Get repository sync if exists (for storing sync relations)
        repo_sync = GithubRepositorySync.objects.filter(
            workspace=workspace,
            project=project,
            repository__repository_id=metadata.get("repository_id")
        ).first()

        imported_issues = []
        imported_labels = {}
        errors = []

        with transaction.atomic():
            for github_issue in github_issues:
                try:
                    # Map GitHub state to Plane state
                    state = map_github_state_to_plane_state(project, github_issue.state)

                    # Convert description markdown to HTML
                    description_html = convert_markdown_to_html(github_issue.body)

                    # Create or update issue
                    issue, created = Issue.objects.update_or_create(
                        workspace=workspace,
                        project=project,
                        external_source="github",
                        external_id=str(github_issue.number),
                        defaults={
                            "name": github_issue.title[:255],  # Limit to 255 chars
                            "description_html": description_html,
                            "description_stripped": github_issue.body[:10000] if github_issue.body else "",
                            "state": state,
                            "created_by": initiated_by,
                            "updated_by": initiated_by,
                        }
                    )

                    # Handle labels
                    for label_data in github_issue.labels:
                        label_name = label_data.get("name", "")
                        if label_name not in imported_labels:
                            imported_labels[label_name] = get_or_create_label(
                                workspace, project, label_data, initiated_by
                            )

                        label = imported_labels[label_name]
                        IssueLabel.objects.get_or_create(
                            workspace=workspace,
                            project=project,
                            issue=issue,
                            label=label,
                            defaults={
                                "created_by": initiated_by,
                                "updated_by": initiated_by,
                            }
                        )

                    # Handle assignees
                    for assignee_data in github_issue.assignees:
                        github_username = assignee_data.get("login", "")
                        plane_user = find_user_by_mapping(workspace, github_username, user_data)

                        if plane_user:
                            IssueAssignee.objects.get_or_create(
                                workspace=workspace,
                                project=project,
                                issue=issue,
                                assignee=plane_user,
                                defaults={
                                    "created_by": initiated_by,
                                    "updated_by": initiated_by,
                                }
                            )

                    # Create GithubIssueSync record if repo sync exists
                    if repo_sync:
                        GithubIssueSync.objects.get_or_create(
                            workspace=workspace,
                            project=project,
                            repository_sync=repo_sync,
                            issue=issue,
                            defaults={
                                "repo_issue_id": github_issue.number,
                                "github_issue_id": github_issue.id,
                                "issue_url": github_issue.html_url,
                                "created_by": initiated_by,
                                "updated_by": initiated_by,
                            }
                        )

                    imported_issues.append({
                        "github_number": github_issue.number,
                        "plane_issue_id": str(issue.id),
                        "title": github_issue.title,
                    })

                except Exception as e:
                    log_exception(e)
                    errors.append({
                        "github_number": github_issue.number,
                        "error": str(e),
                    })

        # Import comments for each issue (separate transaction to avoid long locks)
        if repo_sync:
            for issue_info in imported_issues:
                try:
                    import_issue_comments(
                        github_client=github_client,
                        owner=owner,
                        repo_name=repo_name,
                        github_issue_number=issue_info["github_number"],
                        plane_issue_id=issue_info["plane_issue_id"],
                        workspace=workspace,
                        project=project,
                        repo_sync=repo_sync,
                        initiated_by=initiated_by,
                    )
                except Exception as e:
                    log_exception(e)
                    errors.append({
                        "github_number": issue_info["github_number"],
                        "error": f"Comment import failed: {str(e)}",
                    })

        # Update importer with results
        importer.status = "completed"
        importer.imported_data = {
            "issues_imported": len(imported_issues),
            "labels_imported": len(imported_labels),
            "errors": errors,
            "issues": imported_issues,
        }
        importer.save(update_fields=["status", "imported_data"])

    except GitHubAPIError as e:
        log_exception(e)
        importer = Importer.objects.get(pk=importer_id)
        importer.status = "failed"
        importer.imported_data = {"error": f"GitHub API Error: {str(e)}"}
        importer.save(update_fields=["status", "imported_data"])

    except Exception as e:
        log_exception(e)
        try:
            importer = Importer.objects.get(pk=importer_id)
            importer.status = "failed"
            importer.imported_data = {"error": str(e)}
            importer.save(update_fields=["status", "imported_data"])
        except Exception:
            pass


def import_issue_comments(
    github_client: GitHubAPIClient,
    owner: str,
    repo_name: str,
    github_issue_number: int,
    plane_issue_id: str,
    workspace: Workspace,
    project: Project,
    repo_sync: GithubRepositorySync,
    initiated_by: User,
):
    """Import comments for a specific issue."""
    comments = github_client.get_issue_comments(owner, repo_name, github_issue_number)

    issue = Issue.objects.get(pk=plane_issue_id)
    issue_sync = GithubIssueSync.objects.filter(
        repository_sync=repo_sync,
        issue=issue,
    ).first()

    if not issue_sync:
        return

    for comment_data in comments:
        comment_body = comment_data.get("body", "")
        comment_html = convert_markdown_to_html(comment_body)

        # Create issue comment
        issue_comment, created = IssueComment.objects.get_or_create(
            workspace=workspace,
            project=project,
            issue=issue,
            external_source="github",
            external_id=str(comment_data.get("id")),
            defaults={
                "comment_html": comment_html,
                "actor": initiated_by,
                "created_by": initiated_by,
                "updated_by": initiated_by,
            }
        )

        # Create GithubCommentSync record
        if created:
            GithubCommentSync.objects.get_or_create(
                workspace=workspace,
                project=project,
                issue_sync=issue_sync,
                comment=issue_comment,
                defaults={
                    "repo_comment_id": comment_data.get("id"),
                    "created_by": initiated_by,
                    "updated_by": initiated_by,
                }
            )
