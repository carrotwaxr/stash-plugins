"""GraphQL client for Stash API - Scene File Deduper."""

import requests


class StashClient:
    """Client for interacting with Stash GraphQL API."""

    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/") + "/graphql"
        self.headers = {
            "Content-Type": "application/json",
            "ApiKey": api_key,
        }

    def _execute(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query and return the data."""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        response = requests.post(self.url, json=payload, headers=self.headers, timeout=30)
        response.raise_for_status()

        result = response.json()
        if "errors" in result:
            raise RuntimeError(f"GraphQL error: {result['errors']}")

        return result["data"]

    def test_connection(self) -> bool:
        """Test connection to Stash. Returns True if successful."""
        query = "query { systemStatus { databaseSchema } }"
        self._execute(query)
        return True

    def get_all_tags(self) -> list[dict]:
        """Fetch all tags for autocomplete."""
        query = """
        query AllTags {
          allTags {
            id
            name
          }
        }
        """
        data = self._execute(query)
        return data["allTags"]

    def get_multi_file_scenes(self, exclude_tag_ids: list[str] | None = None) -> list[dict]:
        """Fetch all scenes with more than one file, optionally excluding scenes with certain tags."""
        query = """
        query MultiFileScenes($scene_filter: SceneFilterType) {
          findScenes(scene_filter: $scene_filter, filter: { per_page: -1 }) {
            scenes {
              id
              title
              files {
                id
                path
                basename
                size
                duration
                video_codec
                audio_codec
                width
                height
                frame_rate
                bit_rate
              }
              performers {
                id
                name
              }
              studio {
                id
                name
              }
              tags {
                id
                name
              }
            }
          }
        }
        """
        # Build the filter using variables (safer than string interpolation)
        scene_filter: dict = {"file_count": {"value": 1, "modifier": "GREATER_THAN"}}
        if exclude_tag_ids:
            scene_filter["tags"] = {"value": exclude_tag_ids, "modifier": "EXCLUDES"}

        data = self._execute(query, {"scene_filter": scene_filter})
        return data["findScenes"]["scenes"]

    def set_scene_primary_file(self, scene_id: str, file_id: str) -> None:
        """Set the primary file for a scene."""
        query = """
        mutation SetPrimaryFile($id: ID!, $primary_file_id: ID!) {
          sceneUpdate(input: { id: $id, primary_file_id: $primary_file_id }) {
            id
          }
        }
        """
        self._execute(query, {"id": scene_id, "primary_file_id": file_id})

    def delete_files(self, file_ids: list[str]) -> bool:
        """Delete files by ID. Returns True if successful."""
        query = """
        mutation DeleteFiles($ids: [ID!]!) {
          deleteFiles(ids: $ids)
        }
        """
        data = self._execute(query, {"ids": file_ids})
        return data["deleteFiles"]

    def delete_scene_files(
        self,
        scene_id: str,
        file_ids_to_delete: list[str],
        keep_file_id: str,
        all_file_ids: list[str],
    ) -> bool:
        """
        Delete specified files from a scene, handling primary file logic.

        If the primary file (first in list) is being deleted, we first set
        the keep_file_id as primary, then delete the others.

        Args:
            scene_id: The scene ID
            file_ids_to_delete: File IDs to delete
            keep_file_id: The file ID to keep (will be set as primary if needed)
            all_file_ids: All file IDs in order (first is primary)
        """
        primary_file_id = all_file_ids[0] if all_file_ids else None

        # If we're deleting the primary file, set the keep file as primary first
        if primary_file_id in file_ids_to_delete:
            self.set_scene_primary_file(scene_id, keep_file_id)

        # Now delete the files
        return self.delete_files(file_ids_to_delete)
