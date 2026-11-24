# scripts/test_semantic_module.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.services.semantic_search import SemanticSearchEngine

engine = SemanticSearchEngine(".")
print("Available:", engine.is_available())
print("\n--- Indexing ---")
print(engine.index_project())
print("\n--- Query 1 ---")
print(engine.search("backup file sebelum menimpa"))
print("\n--- Query 2 ---")
print(engine.search("UI panel chat dan pengiriman pesan"))