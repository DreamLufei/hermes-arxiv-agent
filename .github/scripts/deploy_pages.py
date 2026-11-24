#!/usr/bin/env python3
"""Deploy the exact uploaded Pages artifact using GitHub's documented REST API.

Avoid an artifact-list lookup: the upload action already supplies its immutable ID.
Validate the downloadable archive before requesting deployment; never log tokens.
"""
from __future__ import annotations

import io
import json
import os
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            redirected.remove_header("Authorization")
        return redirected


OPENER = urllib.request.build_opener(SafeRedirect())


def request(url: str, token: str, payload: dict | None = None, binary: bool = False):
    headers = {"Accept": "application/vnd.github+json", "Authorization": "Bearer " + token, "User-Agent": "arxiv-pages-publisher"}
    if url.startswith(API + "/"):
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    body = None if payload is None else json.dumps(payload).encode()
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with OPENER.open(req, timeout=60) as response:
        data = response.read()
    return data if binary else json.loads(data)


def validate_archive(data: bytes, expected: dict) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None, "Pages ZIP failed integrity verification"
        tar_names = [name for name in archive.namelist() if name.endswith("artifact.tar")]
        assert len(tar_names) == 1, "Expected a single artifact.tar"
        tar_data = archive.read(tar_names[0])
    with tarfile.open(fileobj=io.BytesIO(tar_data), mode="r:*") as archive:
        members = archive.getmembers()
        assert not any(member.issym() or member.islnk() for member in members), "Pages archive contains a link"
        by_name = {member.name.removeprefix("./"): member for member in members}
        assert "index.html" in by_name and "papers_data.json" in by_name
        data_file = archive.extractfile(by_name["papers_data.json"])
        assert data_file is not None
        assert json.load(data_file) == expected, "Uploaded Pages data differs from the checked-out dataset"


def deploy() -> None:
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["GITHUB_SHA"]
    artifact_id = int(os.environ["PAGES_ARTIFACT_ID"])
    root = Path(os.environ.get("GITHUB_WORKSPACE", "."))
    expected = json.loads((root / "viewer/papers_data.json").read_text())
    artifact_url = f"{API}/repos/{repo}/actions/artifacts/{artifact_id}"
    for attempt in range(1, 7):
        try:
            metadata = request(artifact_url, token)
            assert not metadata["expired"] and metadata["name"] == "github-pages"
            validate_archive(request(artifact_url + "/zip", token, binary=True), expected)
            print(f"Verified Pages artifact {artifact_id}: {expected['count']} papers", flush=True)
            break
        except (urllib.error.URLError, AssertionError, zipfile.BadZipFile, tarfile.TarError) as exc:
            if attempt == 6:
                raise RuntimeError("Uploaded Pages artifact could not be verified") from exc
            print(f"Artifact readiness check {attempt} not yet successful; retrying", flush=True)
            time.sleep(min(5 * attempt, 20))

    for attempt in range(1, 4):
        oidc = request(os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"], os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"])["value"]
        deployment = request(f"{API}/repos/{repo}/pages/deployments", token, {
            "artifact_id": artifact_id,
            "pages_build_version": sha,
            "oidc_token": oidc,
        })
        status_url = deployment.get("status_url")
        if not status_url:
            status_url = f"{API}/repos/{repo}/pages/deployments/{deployment['id']}"
        page_url = deployment.get("page_url", f"https://{repo.split('/')[0].lower()}.github.io/{repo.split('/')[1]}/")
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            status = request(status_url, token)
            outcome = status.get("status", "unknown_status")
            print(f"Pages deployment attempt {attempt}: {outcome}", flush=True)
            if outcome == "succeed":
                output = os.environ.get("GITHUB_OUTPUT")
                if output:
                    with open(output, "a", encoding="utf-8") as stream:
                        stream.write("page_url=" + page_url + "\n")
                print("Pages deployment verified successful", flush=True)
                return
            if outcome in {"deployment_perms_error", "deployment_content_failed", "deployment_cancelled"}:
                raise RuntimeError("Pages deployment cannot continue: " + outcome)
            if outcome in {"deployment_failed", "deployment_lost"}:
                break
            time.sleep(5)
        else:
            raise TimeoutError("Pages deployment did not reach a final state")
        if attempt < 3:
            time.sleep(10 * attempt)
    raise RuntimeError("Pages backend rejected three verified-artifact deployment attempts")


if __name__ == "__main__":
    deploy()
