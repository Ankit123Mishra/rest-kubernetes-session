#!/usr/bin/env bash
# Walk through every REST lesson with curl, one step at a time.
#   ./demo.sh                       → talks to localhost:8001 / 8002
#   MENU=http://localhost:9001 ORDER=http://localhost:9002 ./demo.sh
set -u
MENU=${MENU:-http://localhost:8001}
ORDER=${ORDER:-http://localhost:8002}
KEY="X-API-Key: barista-secret"
JSON="Content-Type: application/json"

step() { printf '\n\033[1;36m▶ %s\033[0m\n\033[2m$ %s\033[0m\n' "$1" "$2"; read -rp "  (enter to run) "; eval "$2"; echo; }

step "GET a collection, with filters + pagination" \
  "curl -s '$MENU/menu?category=espresso&limit=2' | python3 -m json.tool"

step "GET one resource (-i shows status + headers, note X-Served-By)" \
  "curl -si $MENU/menu/2"

step "GET something that doesn't exist → 404" \
  "curl -si $MENU/menu/999"

step "POST without the API key → 401" \
  "curl -si -X POST $MENU/menu -H '$JSON' -d '{\"name\":\"Mocha\",\"category\":\"espresso\",\"price_cents\":520}'"

step "POST with bad data → 422 (Pydantic did this, we wrote no code)" \
  "curl -si -X POST $MENU/menu -H '$KEY' -H '$JSON' -d '{\"name\":\"M\",\"category\":\"smoothie\",\"price_cents\":-5}'"

step "POST a valid item → 201 Created + Location header" \
  "curl -si -X POST $MENU/menu -H '$KEY' -H '$JSON' -d '{\"name\":\"Mocha\",\"category\":\"espresso\",\"price_cents\":520}'"

step "POST the same item again → 409 Conflict (POST is not idempotent)" \
  "curl -si -X POST $MENU/menu -H '$KEY' -H '$JSON' -d '{\"name\":\"Mocha\",\"category\":\"espresso\",\"price_cents\":520}'"

step "PUT replaces the whole item (run it twice, same result: idempotent)" \
  "curl -s -X PUT $MENU/menu/8 -H '$KEY' -H '$JSON' -d '{\"name\":\"Mocha\",\"category\":\"espresso\",\"price_cents\":550,\"available\":true}' | python3 -m json.tool"

step "Path + query + header + body in one call: preview a price change without saving" \
  "curl -s -X PATCH '$MENU/menu/2?dry_run=true' -H '$KEY' -H '$JSON' -d '{\"price_cents\":400}' | python3 -m json.tool && curl -s $MENU/menu/2 | python3 -m json.tool"

step "PATCH changes only what you send" \
  "curl -s -X PATCH $MENU/menu/7 -H '$KEY' -H '$JSON' -d '{\"available\":false}' | python3 -m json.tool"

step "DELETE → 204 No Content" \
  "curl -si -X DELETE $MENU/menu/8 -H '$KEY'"

step "Service-to-service: order-service calls menu-service for the price" \
  "curl -s -X POST $ORDER/orders -H '$JSON' -d '{\"customer\":\"Asha\",\"item_id\":2,\"quantity\":2}' | python3 -m json.tool"

step "Order a sold-out item (we PATCHed the croissant) → 409 from order-service" \
  "curl -si -X POST $ORDER/orders -H '$JSON' -d '{\"customer\":\"Ben\",\"item_id\":7}'"

step "Order an item that doesn't exist → menu says 404, order-service says 422" \
  "curl -si -X POST $ORDER/orders -H '$JSON' -d '{\"customer\":\"Ben\",\"item_id\":999}'"

echo "Done. Open $MENU/docs to try everything in the browser."
