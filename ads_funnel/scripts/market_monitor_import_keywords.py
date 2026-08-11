"""Import the Xydc three-tier keyword catalog into the market monitor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ads_funnel.api.market_monitoring import repository, schema  # noqa: E402
from ads_funnel.api.market_monitoring.constants import (  # noqa: E402
    DEFAULT_MARKET_ID,
    KEYWORD_SOURCE_VERSION,
    KEYWORD_WORKBOOK_PATH,
)
from ads_funnel.api.market_monitoring.keyword_catalog import (  # noqa: E402
    load_keyword_catalog,
    validate_keyword_catalog,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import the Xydc keyword catalog.")
    parser.add_argument("--workbook", type=Path, default=KEYWORD_WORKBOOK_PATH)
    parser.add_argument("--market-id", default=DEFAULT_MARKET_ID)
    parser.add_argument("--source-version", default=KEYWORD_SOURCE_VERSION)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = load_keyword_catalog(args.workbook)
    counts = validate_keyword_catalog(rows)
    if args.dry_run:
        print(json.dumps({"active": len(rows), **counts}, ensure_ascii=False))
        return 0

    schema.init_market_db(seed_defaults=False)
    result = repository.sync_keyword_catalog(args.market_id, rows, args.source_version)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
