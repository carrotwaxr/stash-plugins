"""GraphQL client for Stash API."""

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

    def get_all_performers(self) -> list[dict]:
        """Fetch all performers with stash_ids."""
        query = """
        query AllPerformersWithStashIDs {
          findPerformers(filter: { per_page: -1 }) {
            performers {
              id
              name
              alias_list
              gender
              country
              scene_count
              image_count
              gallery_count
              stash_ids {
                endpoint
                stash_id
              }
            }
          }
        }
        """
        data = self._execute(query)
        return data["findPerformers"]["performers"]

    def get_performer(self, performer_id: str) -> dict:
        """Get a performer by ID with full details."""
        query = """
        query GetPerformer($id: ID!) {
          findPerformer(id: $id) {
            id
            name
            alias_list
          }
        }
        """
        data = self._execute(query, {"id": performer_id})
        return data["findPerformer"]

    def get_performer_scenes(self, performer_id: str) -> list[dict]:
        """Get all scenes for a performer."""
        query = """
        query GetPerformerScenes($id: ID!) {
          findScenes(scene_filter: { performers: { value: [$id], modifier: INCLUDES } }, filter: { per_page: -1 }) {
            scenes {
              id
              performers { id }
            }
          }
        }
        """
        data = self._execute(query, {"id": performer_id})
        return data["findScenes"]["scenes"]

    def get_performer_images(self, performer_id: str) -> list[dict]:
        """Get all images for a performer."""
        query = """
        query GetPerformerImages($id: ID!) {
          findImages(image_filter: { performers: { value: [$id], modifier: INCLUDES } }, filter: { per_page: -1 }) {
            images {
              id
              performers { id }
            }
          }
        }
        """
        data = self._execute(query, {"id": performer_id})
        return data["findImages"]["images"]

    def get_performer_galleries(self, performer_id: str) -> list[dict]:
        """Get all galleries for a performer."""
        query = """
        query GetPerformerGalleries($id: ID!) {
          findGalleries(gallery_filter: { performers: { value: [$id], modifier: INCLUDES } }, filter: { per_page: -1 }) {
            galleries {
              id
              performers { id }
            }
          }
        }
        """
        data = self._execute(query, {"id": performer_id})
        return data["findGalleries"]["galleries"]

    def update_scene_performers(self, scene_id: str, performer_ids: list[str]) -> None:
        """Update the performers for a scene."""
        query = """
        mutation UpdateScene($id: ID!, $performer_ids: [ID!]) {
          sceneUpdate(input: { id: $id, performer_ids: $performer_ids }) {
            id
          }
        }
        """
        self._execute(query, {"id": scene_id, "performer_ids": performer_ids})

    def update_image_performers(self, image_id: str, performer_ids: list[str]) -> None:
        """Update the performers for an image."""
        query = """
        mutation UpdateImage($id: ID!, $performer_ids: [ID!]) {
          imageUpdate(input: { id: $id, performer_ids: $performer_ids }) {
            id
          }
        }
        """
        self._execute(query, {"id": image_id, "performer_ids": performer_ids})

    def update_gallery_performers(self, gallery_id: str, performer_ids: list[str]) -> None:
        """Update the performers for a gallery."""
        query = """
        mutation UpdateGallery($id: ID!, $performer_ids: [ID!]) {
          galleryUpdate(input: { id: $id, performer_ids: $performer_ids }) {
            id
          }
        }
        """
        self._execute(query, {"id": gallery_id, "performer_ids": performer_ids})

    def update_performer_aliases(self, performer_id: str, aliases: list[str]) -> None:
        """Update the aliases for a performer."""
        query = """
        mutation UpdatePerformer($id: ID!, $alias_list: [String!]) {
          performerUpdate(input: { id: $id, alias_list: $alias_list }) {
            id
          }
        }
        """
        self._execute(query, {"id": performer_id, "alias_list": aliases})

    def merge_performers(self, source_ids: list[str], destination_id: str) -> dict:
        """
        Merge source performers into destination performer.

        This manually reassigns all scenes/images/galleries from source performers
        to the destination, and merges aliases.

        TODO: Switch to native performerMerge mutation once Stash releases it
        (added in PR #5910, expected in a version after v0.30.1)
        """
        # Get destination performer info
        dest = self.get_performer(destination_id)
        dest_aliases = set(dest.get("alias_list") or [])

        for source_id in source_ids:
            # Get source performer info
            source = self.get_performer(source_id)
            source_name = source.get("name", "")
            source_aliases = source.get("alias_list") or []

            # Add source name and aliases to destination aliases
            if source_name and source_name != dest["name"]:
                dest_aliases.add(source_name)
            dest_aliases.update(source_aliases)

            # Reassign scenes
            scenes = self.get_performer_scenes(source_id)
            for scene in scenes:
                current_performer_ids = [p["id"] for p in scene["performers"]]
                # Remove source, add destination if not already present
                new_performer_ids = [pid for pid in current_performer_ids if pid != source_id]
                if destination_id not in new_performer_ids:
                    new_performer_ids.append(destination_id)
                if set(new_performer_ids) != set(current_performer_ids):
                    self.update_scene_performers(scene["id"], new_performer_ids)

            # Reassign images
            images = self.get_performer_images(source_id)
            for image in images:
                current_performer_ids = [p["id"] for p in image["performers"]]
                new_performer_ids = [pid for pid in current_performer_ids if pid != source_id]
                if destination_id not in new_performer_ids:
                    new_performer_ids.append(destination_id)
                if set(new_performer_ids) != set(current_performer_ids):
                    self.update_image_performers(image["id"], new_performer_ids)

            # Reassign galleries
            galleries = self.get_performer_galleries(source_id)
            for gallery in galleries:
                current_performer_ids = [p["id"] for p in gallery["performers"]]
                new_performer_ids = [pid for pid in current_performer_ids if pid != source_id]
                if destination_id not in new_performer_ids:
                    new_performer_ids.append(destination_id)
                if set(new_performer_ids) != set(current_performer_ids):
                    self.update_gallery_performers(gallery["id"], new_performer_ids)

        # Update destination performer's aliases
        # Remove destination's own name from aliases if present
        dest_aliases.discard(dest["name"])
        if dest_aliases:
            self.update_performer_aliases(destination_id, list(dest_aliases))

        return {"id": destination_id, "name": dest["name"]}
