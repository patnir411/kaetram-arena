"""Login and registration flow for the Kaetram game client."""

import json
import os

from mcp.server.fastmcp import Context

from mcp_server.core import log, log_tool


async def _attempt_register(page, username: str, password: str) -> None:
    """Fill + submit the register form. Returns when the form was clicked; the
    caller's attempt loop will detect success/failure."""
    await page.evaluate("""({username, password}) => {
        const btn = document.getElementById('new-account');
        if (btn) btn.click();
        setTimeout(() => {
            const set = (el, val) => {
                if (!el) return;
                Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
                    .set.call(el, val);
                el.dispatchEvent(new Event('input', {bubbles: true}));
            };
            set(document.getElementById('register-name-input'), username);
            set(document.getElementById('register-password-input'), password);
            set(document.getElementById('register-password-confirmation-input'), password);
            set(document.getElementById('register-email-input'), username + '@test.com');
            setTimeout(() => {
                const play = document.getElementById('play');
                if (play) play.click();
            }, 500);
        }, 500);
    }""", {"username": username, "password": password})


async def login_impl(ctx: Context, page) -> str:
    username = os.environ.get("KAETRAM_USERNAME", "ClaudeBot")
    # Default password matches bench/seed.py:FIXED_BCRYPT_HASH (bcrypt of "test").
    # "password123" was a legacy default that would silently produce invalidlogin
    # against any seeded account.
    password = os.environ.get("KAETRAM_PASSWORD", "test")
    client_url = os.environ.get("KAETRAM_CLIENT_URL", "http://localhost:9000")

    # Surface the resolved credentials on every login attempt. Hardcoded env
    # in opencode.json was silently overriding KAETRAM_PASSWORD with the wrong
    # value for weeks, and without this log line the mismatch was invisible.
    # Password is logged by length only — never in plaintext.
    log(
        f"[mcp] login env: username={username} "
        f"password=<{len(password)} chars> "
        f"client_url={client_url} "
        f"port={os.environ.get('KAETRAM_PORT', '(unset)')}"
    )

    await page.goto(client_url)
    try:
        await page.wait_for_function(
            """() => (
                document.body?.className === 'game' ||
                !!document.getElementById('login-name-input')
            )""",
            timeout=10000,
        )
    except Exception:
        log("[mcp] login readiness wait timed out; trying login form anyway")
    await page.locator("#login-name-input").fill(username)
    await page.locator("#login-password-input").fill(password)
    await page.locator("#login").click()

    # Login retry + fallback-register loop.
    #
    # We auto-register ONLY on explicit "account not found" style errors.
    # Historically we also fired register on any 6-second silence, which
    # collided with two common states:
    #   (1) the game server is still hydrating regions after a restart, so
    #       the login form stays visible without an error — in which case
    #       register would fire and collide with a seed that IS in Mongo;
    #   (2) Kaetram returned "already logged in" (the server still holds a
    #       ghost session from the previous agent) — register would fire
    #       and get rejected with "userexists".
    # Both failure modes look like "account already exists" in the UI.
    #
    # The new policy is: recognize every documented error code explicitly,
    # retry idle states patiently, and only fall back to register when the
    # login form is still visible AND the last known error was actually a
    # "not found" style code (not silence, not "already logged in").
    game_ready = False
    login_error: str | None = None
    tried_register = False
    saw_not_found = False
    for _attempt in range(18):
        await page.wait_for_timeout(1000)
        result = await page.evaluate("""() => {
            const game = document.body.className === 'game';
            const lc = document.getElementById('load-character');
            const loginVisible = lc && window.getComputedStyle(lc).opacity !== '0';
            const err = document.getElementById('login-error-text');
            const errText = err ? (err.textContent || '').trim() : '';
            return { game, loginVisible, errText };
        }""")
        log(f"[mcp] login attempt {_attempt+1}: {result}")
        if result.get("game"):
            game_ready = True
            break
        err_text = (result.get("errText") or "").lower()
        if err_text:
            login_error = err_text

            # Client messages come from
            # Kaetram-Open/packages/client/src/network/messages.ts. We match the
            # exact phrases emitted there rather than fuzzy substrings.
            not_found = (
                "not found" in err_text
                or "does not exist" in err_text
                or "unknown" in err_text
                # invalidlogin → "wrong username or password" — this fires both
                # when the username is missing AND when the password is wrong.
                # On a fresh/wiped Mongo the account is missing so registering
                # is the right move; on a stale-seed mismatch we'd rather die
                # loud than try to register and potentially collide.
                or ("wrong username or password" in err_text and not tried_register)
            )
            already_registered = "already exists" in err_text
            already_online = "already logged in" in err_text or "already online" in err_text

            if already_registered:
                # Don't retry — if we're here from a register attempt, Mongo
                # has a row that wasn't cleaned up. Stop and surface the
                # actual state so the operator can investigate.
                log(f"[mcp] register rejected — account already exists for {username}")
                break
            if already_online:
                # Stale ghost session on the server side. Give Kaetram a few
                # seconds to time out the old socket and retry login rather
                # than registering over top of our existing account.
                log("[mcp] server reports already-logged-in; waiting for ghost session to clear")
                await page.wait_for_timeout(3000)
                await page.locator("#login").click()
                continue
            if not_found:
                saw_not_found = True
                if not tried_register:
                    tried_register = True
                    log(f"[mcp] login rejected ({err_text!r}); attempting register for {username}")
                    await _attempt_register(page, username, password)
                    continue
                # Already tried register once — don't double-try.
                break
            if "incorrect password" in err_text or "wrong password" in err_text:
                break

        # Login form is still up but there's no error yet. Keep retrying —
        # the game server may just be slow to hydrate regions after a restart.
        # We no longer fire a speculative register after 6 idle seconds; that
        # path caused the "userexists" collision whenever Mongo wasn't truly
        # empty.

    if not game_ready:
        detail = f" ({login_error})" if login_error else ""
        log(f"[mcp] Login failed for {username}{detail}")
        log_tool("login", success=False, error=login_error or "game did not load")
        ctx.request_context.lifespan_context["logged_in"] = False
        ws_port = os.environ.get("KAETRAM_PORT", "(unset)")
        return (
            f"Login FAILED for {username}{detail}. "
            "Check KAETRAM_PASSWORD env matches the seeded bcrypt hash; "
            f"make sure the game server on :{ws_port} is reachable and no other "
            "session holds this username."
        )

    # Wait for the game world to fully hydrate
    for _hydrate in range(10):
        await page.wait_for_timeout(500)
        try:
            hydrated = await page.evaluate("""() => {
                const g = window.game;
                if (!g || !g.player) return { ready: false };
                const ents = g.entities;
                const entCount = ents ? Object.keys(ents.entities || {}).length : 0;
                return {
                    ready: true,
                    regionsLoaded: (g.map && g.map.regionsLoaded) || 0,
                    entCount,
                };
            }""")
            if hydrated.get("ready") and hydrated.get("regionsLoaded", 0) > 0 and hydrated.get("entCount", 0) > 1:
                log(f"[mcp] World hydrated after {(_hydrate+1)*500}ms: regions={hydrated['regionsLoaded']}, entities={hydrated['entCount']}")
                break
        except Exception:
            pass
    else:
        log("[mcp] World hydration timed out (5s) — proceeding anyway")

    await page.keyboard.press("Escape")
    await page.wait_for_timeout(100)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(100)

    ctx.request_context.lifespan_context["logged_in"] = True
    log(f"[mcp] Logged in as {username}")

    try:
        post_login_state = await page.evaluate("""() => {
            const game = window.game;
            const p = game && game.player;
            if (!p) return null;
            const questState = {};
            for (const key of ["tutorial", "sorcery"]) {
                const q = p.quests && p.quests[key];
                questState[key] = q ? {
                    stage: q.stage || 0,
                    subStage: q.subStage || 0,
                    stageCount: q.stageCount || 0,
                    completedSubStages: q.completedSubStages || [],
                    finished: (q.stage || 0) >= (q.stageCount || 0),
                } : null;
            }
            return {
                username: p.username || p.name || null,
                position: {
                    gridX: p.gridX,
                    gridY: p.gridY,
                    x: p.x,
                    y: p.y,
                    oldX: p.oldX,
                    oldY: p.oldY,
                },
                ready: !!p.ready,
                dead: !!p.dead,
                hitPoints: p.hitPoints,
                maxHitPoints: p.maxHitPoints,
                quests: questState,
                map: {
                    loaded: !!game.map,
                    regionsLoaded: game.map && game.map.regionsLoaded,
                    width: game.map && game.map.width,
                    height: game.map && game.map.height,
                },
            };
        }""")
        log(f"[mcp][debug_login] post-login state: {json.dumps(post_login_state, sort_keys=True)[:2000]}")
    except Exception as e:
        log(f"[mcp][debug_login] post-login state unavailable: {e}")

    # Auto-warp out of the tutorial spawn
    try:
        tutorial_state = await page.evaluate("""() => {
            const p = window.game && window.game.player;
            if (!p) return null;
            const q = p.quests && p.quests.tutorial;
            return {
                pos: { x: p.gridX, y: p.gridY },
                tutorial: q ? {
                    stage: q.stage || 0,
                    stageCount: q.stageCount || 0,
                    finished: (q.stage || 0) >= (q.stageCount || 0),
                } : null,
            };
        }""")
        log(f"[mcp][debug_login] tutorial auto-warp check: {json.dumps(tutorial_state, sort_keys=True)[:1000]}")
        pos = (tutorial_state or {}).get("pos")
        # Auto-warp anyone parked at the tutorial spawn (Programmer's house).
        # Pre-seeded accounts via tests/e2e/helpers/seed.py mark the tutorial
        # finished but still spawn the character at (328, 892), so the prior
        # `tutorial_unfinished`-only check left fresh-seeded bots stuck in the
        # corner. The coordinate gate alone is sufficient — no real gameplay
        # happens at those tiles.
        if (
            pos
            and 300 <= pos.get("x", 0) <= 360
            and 860 <= pos.get("y", 0) <= 920
        ):
            log(f"[mcp] Tutorial-spawn coords detected at {pos}; auto-warping to Mudwich")
            await page.evaluate("(id) => window.__safeWarp(id)", 0)
            await page.wait_for_timeout(2500)
    except Exception as e:
        log(f"[mcp] tutorial auto-warp skipped: {e}")

    log_tool("login")
    return f"Logged in as {username}"
