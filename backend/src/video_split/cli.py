from __future__ import annotations

import json
import sys

import httpx
import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="vsplit", help="Video Split View CLI")
console = Console()

_base_url = "http://localhost:8080"
_token: str | None = None


def _headers() -> dict[str, str]:
    if _token:
        return {"Authorization": f"Bearer {_token}"}
    return {}


def _client() -> httpx.Client:
    return httpx.Client(base_url=_base_url, headers=_headers(), timeout=300)


@app.command()
def login(username: str = typer.Option(...), password: str = typer.Option(..., prompt=True, hide_input=True)):
    """Login to get access token."""
    global _token
    with _client() as client:
        resp = client.post("/api/auth/login", json={"username": username, "password": password})
        if resp.status_code != 200:
            console.print(f"[red]Login failed: {resp.json().get('detail', resp.text)}[/red]")
            raise typer.Exit(1)
        data = resp.json()
        _token = data["access_token"]
        console.print(f"[green]Logged in as {data['username']} (role: {data['role']})[/green]")
        console.print(f"Token: {_token}")


@app.command()
def register(username: str = typer.Option(...), password: str = typer.Option(..., prompt=True, hide_input=True)):
    """Register a new user."""
    global _token
    with _client() as client:
        resp = client.post("/api/auth/register", json={"username": username, "password": password})
        if resp.status_code != 200:
            console.print(f"[red]Registration failed: {resp.json().get('detail', resp.text)}[/red]")
            raise typer.Exit(1)
        data = resp.json()
        _token = data["access_token"]
        console.print(f"[green]Registered and logged in as {data['username']}[/green]")


@app.command()
def analyze(
    url: str = typer.Argument(..., help="Video URL to analyze"),
    token: str = typer.Option("", help="JWT token"),
):
    """Analyze a video URL."""
    headers = {"Authorization": f"Bearer {token}"} if token else _headers()
    if not headers.get("Authorization"):
        console.print("[red]Please provide --token or run login first[/red]")
        raise typer.Exit(1)

    with httpx.Client(base_url=_base_url, timeout=600) as client:
        with client.stream(
            "POST", "/api/videos/analyze",
            json={"url": url},
            headers=headers,
        ) as resp:
            if resp.status_code != 200:
                console.print(f"[red]Error: {resp.text}[/red]")
                raise typer.Exit(1)
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    stage = data.get("stage", "")
                    msg = data.get("message", "")
                    progress = data.get("progress", 0)
                    console.print(f"  [{progress:5.1f}%] {stage}: {msg}")
                    if stage == "complete":
                        vid = data.get("detail", {}).get("video_id")
                        console.print(f"[green]Analysis complete! Video ID: {vid}[/green]")
                    elif stage == "error":
                        console.print(f"[red]Error: {msg}[/red]")


@app.command(name="list")
def list_videos(
    public: bool = typer.Option(False, help="Show public videos"),
    tag: str = typer.Option("", help="Filter by tag"),
    token: str = typer.Option("", help="JWT token"),
):
    """List saved videos."""
    headers = {"Authorization": f"Bearer {token}"} if token else _headers()
    endpoint = "/api/videos/public" if public else "/api/videos"
    params: dict[str, str] = {}
    if tag:
        params["tag"] = tag

    with httpx.Client(base_url=_base_url, headers=headers, timeout=30) as client:
        resp = client.get(endpoint, params=params)
        if resp.status_code != 200:
            console.print(f"[red]Error: {resp.text}[/red]")
            raise typer.Exit(1)
        videos = resp.json()

    table = Table(title="Videos")
    table.add_column("ID", style="cyan")
    table.add_column("Title", max_width=50)
    table.add_column("Platform")
    table.add_column("Duration")
    table.add_column("Tags")
    table.add_column("Public")

    for v in videos:
        dur = v["duration_seconds"]
        dur_str = f"{dur // 60}:{dur % 60:02d}"
        tags_str = ", ".join(t["name"] for t in v.get("tags", []))
        table.add_row(
            str(v["id"]), v["title"], v["platform"],
            dur_str, tags_str, "Yes" if v["is_public"] else "No",
        )
    console.print(table)


@app.command()
def show(
    video_id: int = typer.Argument(..., help="Video ID"),
    token: str = typer.Option("", help="JWT token"),
):
    """Show video details with segments."""
    headers = {"Authorization": f"Bearer {token}"} if token else _headers()
    with httpx.Client(base_url=_base_url, headers=headers, timeout=30) as client:
        resp = client.get(f"/api/videos/{video_id}")
        if resp.status_code != 200:
            console.print(f"[red]Error: {resp.text}[/red]")
            raise typer.Exit(1)
        v = resp.json()

    console.print(f"\n[bold]{v['title']}[/bold]")
    console.print(f"Platform: {v['platform']} | Duration: {v['duration_seconds'] // 60}m")
    console.print(f"URL: {v['url']}\n")
    console.print(f"[bold]Summary:[/bold]\n{v['summary']}\n")

    table = Table(title="Segments")
    table.add_column("#", style="cyan")
    table.add_column("Time")
    table.add_column("Title")
    table.add_column("Summary", max_width=60)

    for seg in v.get("segments", []):
        start = seg["start_seconds"]
        end = seg["end_seconds"]
        time_str = f"{start // 60}:{start % 60:02d} - {end // 60}:{end % 60:02d}"
        table.add_row(str(seg["segment_index"]), time_str, seg["title"], seg["summary"])
    console.print(table)


@app.command()
def tasks(token: str = typer.Option("", help="JWT token")):
    """List pending/failed tasks."""
    headers = {"Authorization": f"Bearer {token}"} if token else _headers()
    with httpx.Client(base_url=_base_url, headers=headers, timeout=30) as client:
        resp = client.get("/api/videos/tasks")
        if resp.status_code != 200:
            console.print(f"[red]Error: {resp.text}[/red]")
            raise typer.Exit(1)
        task_list = resp.json()

    if not task_list:
        console.print("[green]No pending tasks.[/green]")
        return

    table = Table(title="Tasks")
    table.add_column("ID", style="cyan")
    table.add_column("URL", max_width=40)
    table.add_column("Status")
    table.add_column("Error", max_width=40)

    for t in task_list:
        table.add_row(str(t["id"]), t["url"], t["status"], t.get("error_message", ""))
    console.print(table)


if __name__ == "__main__":
    app()
