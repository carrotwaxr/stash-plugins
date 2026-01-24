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

    def merge_performers(self, source_ids: list[str], destination_id: str) -> dict:
        """Merge source performers into destination performer."""
        query = """
        mutation MergePerformers($source: [ID!]!, $destination: ID!) {
          performerMerge(input: { source: $source, destination: $destination }) {
            id
            name
          }
        }
        """
        variables = {"source": source_ids, "destination": destination_id}
        data = self._execute(query, variables)
        return data["performerMerge"]
