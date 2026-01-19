# Python imports
import requests
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

# Module imports
from plane.utils.exception_logger import log_exception


@dataclass
class GitHubRepository:
    """GitHub Repository data class"""
    id: int
    name: str
    full_name: str
    owner: str
    html_url: str
    description: Optional[str]
    private: bool


@dataclass
class GitHubIssue:
    """GitHub Issue data class"""
    id: int
    number: int
    title: str
    body: Optional[str]
    state: str  # "open" or "closed"
    html_url: str
    labels: List[Dict[str, Any]]
    assignees: List[Dict[str, Any]]
    created_at: str
    updated_at: str
    closed_at: Optional[str]


@dataclass
class GitHubCollaborator:
    """GitHub Collaborator data class"""
    id: int
    login: str
    avatar_url: str
    html_url: str


class GitHubAPIClient:
    """
    GitHub API Client for fetching repositories, issues, and collaborators.
    Uses GitHub REST API v3.
    """

    BASE_URL = "https://api.github.com"

    def __init__(self, access_token: str):
        """
        Initialize GitHub API Client.

        Args:
            access_token: GitHub personal access token or OAuth token
        """
        self.access_token = access_token
        self.headers = {
            "Authorization": f"token {access_token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        timeout: int = 30
    ) -> Optional[Any]:
        """
        Make HTTP request to GitHub API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            params: Query parameters
            timeout: Request timeout in seconds

        Returns:
            Response JSON or None on error
        """
        url = f"{self.BASE_URL}{endpoint}"

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self.headers,
                params=params,
                timeout=timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            log_exception(e)
            raise GitHubAPIError(f"GitHub API request failed: {str(e)}")

    def _paginate(
        self,
        endpoint: str,
        params: Optional[Dict] = None,
        max_pages: int = 10
    ) -> List[Any]:
        """
        Paginate through GitHub API results.

        Args:
            endpoint: API endpoint path
            params: Query parameters
            max_pages: Maximum number of pages to fetch

        Returns:
            List of all results
        """
        if params is None:
            params = {}

        params["per_page"] = 100
        results = []

        for page in range(1, max_pages + 1):
            params["page"] = page
            data = self._make_request("GET", endpoint, params)

            if not data:
                break

            results.extend(data)

            # If we got fewer results than per_page, we've reached the end
            if len(data) < 100:
                break

        return results

    def get_user_repositories(self, page: int = 1, per_page: int = 30) -> List[Dict]:
        """
        Get repositories accessible to the authenticated user.

        Args:
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            List of repository dictionaries
        """
        params = {
            "page": page,
            "per_page": per_page,
            "sort": "updated",
            "direction": "desc",
            "affiliation": "owner,collaborator,organization_member",
        }

        return self._make_request("GET", "/user/repos", params) or []

    def get_repository(self, owner: str, repo: str) -> Optional[Dict]:
        """
        Get a specific repository.

        Args:
            owner: Repository owner username
            repo: Repository name

        Returns:
            Repository dictionary or None
        """
        return self._make_request("GET", f"/repos/{owner}/{repo}")

    def get_repository_issues(
        self,
        owner: str,
        repo: str,
        state: str = "all",
        max_pages: int = 10
    ) -> List[GitHubIssue]:
        """
        Get all issues from a repository.

        Args:
            owner: Repository owner username
            repo: Repository name
            state: Issue state filter ("open", "closed", "all")
            max_pages: Maximum pages to fetch

        Returns:
            List of GitHubIssue objects
        """
        params = {"state": state, "sort": "created", "direction": "asc"}
        issues_data = self._paginate(f"/repos/{owner}/{repo}/issues", params, max_pages)

        issues = []
        for issue_data in issues_data:
            # Skip pull requests (they appear in issues API)
            if "pull_request" in issue_data:
                continue

            issues.append(GitHubIssue(
                id=issue_data["id"],
                number=issue_data["number"],
                title=issue_data["title"],
                body=issue_data.get("body"),
                state=issue_data["state"],
                html_url=issue_data["html_url"],
                labels=issue_data.get("labels", []),
                assignees=issue_data.get("assignees", []),
                created_at=issue_data["created_at"],
                updated_at=issue_data["updated_at"],
                closed_at=issue_data.get("closed_at"),
            ))

        return issues

    def get_repository_labels(self, owner: str, repo: str) -> List[Dict]:
        """
        Get all labels from a repository.

        Args:
            owner: Repository owner username
            repo: Repository name

        Returns:
            List of label dictionaries
        """
        return self._paginate(f"/repos/{owner}/{repo}/labels", max_pages=5)

    def get_repository_collaborators(self, owner: str, repo: str) -> List[GitHubCollaborator]:
        """
        Get all collaborators from a repository.

        Args:
            owner: Repository owner username
            repo: Repository name

        Returns:
            List of GitHubCollaborator objects
        """
        try:
            collaborators_data = self._paginate(f"/repos/{owner}/{repo}/collaborators", max_pages=5)
        except GitHubAPIError:
            # User might not have permission to list collaborators
            # Fall back to getting assignees from issues
            return []

        return [
            GitHubCollaborator(
                id=c["id"],
                login=c["login"],
                avatar_url=c["avatar_url"],
                html_url=c["html_url"],
            )
            for c in collaborators_data
        ]

    def get_issue_comments(self, owner: str, repo: str, issue_number: int) -> List[Dict]:
        """
        Get all comments for an issue.

        Args:
            owner: Repository owner username
            repo: Repository name
            issue_number: Issue number

        Returns:
            List of comment dictionaries
        """
        return self._paginate(
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            max_pages=10
        )

    def get_repository_info(self, owner: str, repo: str) -> Dict:
        """
        Get repository info including issue count, labels, and collaborators.

        Args:
            owner: Repository owner username
            repo: Repository name

        Returns:
            Dictionary with issue_count, labels, and collaborators
        """
        # Get repository details for open issues count
        repo_data = self.get_repository(owner, repo)

        # Get labels
        labels = self.get_repository_labels(owner, repo)

        # Get collaborators
        collaborators = self.get_repository_collaborators(owner, repo)

        return {
            "issue_count": repo_data.get("open_issues_count", 0) if repo_data else 0,
            "labels": len(labels),
            "collaborators": [
                {
                    "id": c.id,
                    "login": c.login,
                    "avatar_url": c.avatar_url,
                    "html_url": c.html_url,
                    "url": f"https://api.github.com/users/{c.login}",
                }
                for c in collaborators
            ],
        }


class GitHubAPIError(Exception):
    """Custom exception for GitHub API errors"""
    pass
