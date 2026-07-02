import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest content into the OpenAI vector store")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--site", action="store_true", help="crawl the configured site's sitemap")
    group.add_argument("--url", help="ingest a single URL")
    group.add_argument("--file", help="ingest a single file (pdf, docx, txt, md)")
    group.add_argument("--list", action="store_true", help="list ingested sources")
    args = parser.parse_args()

    from app.ingestion import openai_store
    from app.ingestion.pipeline import ingest_site, ingest_target

    if args.list:
        sources = openai_store.list_sources()
        print(f"{len(sources)} sources in vector store {openai_store.settings.openai_vector_store_id or '(none)'}:")
        for s in sources:
            print(f"  - {s['source_id']}  ->  {s.get('file_id', '?')}  ({s.get('title', '')})")
        return

    # Make sure a vector store exists (creates + logs an id to pin in .env if unset).
    openai_store.ensure_vector_store()

    failed: list[str] = []
    if args.site:
        results, failed = ingest_site()
    elif args.url:
        results = ingest_target(args.url)
    else:
        path = args.file
        results = ingest_target(
            path,
            source_id=f"file:{Path(path).name}",
            title=Path(path).stem,
            url=Path(path).name,
            raw_path=path,
        )

    total = sum(r.chunks for r in results)
    print(f"\nIngested {len(results)} source(s), {total} file(s) uploaded.")

    if failed:
        # A partial ingest is easy to miss otherwise — print offenders and fail
        # the run so a scheduled/manual re-ingest actually shows red.
        print(f"\n{len(failed)} page(s) FAILED to ingest:")
        for url in failed:
            print(f"  - {url}")
        sys.exit(1)


if __name__ == "__main__":
    main()
