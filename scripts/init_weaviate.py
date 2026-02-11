"""
Initialize Weaviate with schema and basic data.
Run this once to set up the vector database.
"""

from backend.config.settings import settings
from backend.knowledge_base.weaviate_client import WeaviateClient


def main() -> None:
    print("Initializing Weaviate...")
    client = WeaviateClient(url=settings.WEAVIATE_URL, api_key=settings.WEAVIATE_API_KEY)
    print("Creating schema...")
    client.create_schema()
    print("Weaviate initialized successfully!")
    print("\nCollections created:")
    print("- Narratives (for SAR narrative examples)")
    print("- Regulations (for BSA, FinCEN, OFAC guidance)")
    print("- Definitions (for AML terminology)")
    print("\nNext step: Run 'python scripts/seed_kb.py' to populate with data")


if __name__ == "__main__":
    main()
