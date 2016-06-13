import botconfig
import src.settings as var
import sqlite3
import os
import json
from collections import defaultdict

# increment this whenever making a schema change so that the schema upgrade functions run on start
# they do not run by default for performance reasons
SCHEMA_VERSION = 1

def init_vars():
    with var.GRAVEYARD_LOCK:
        c = conn.cursor()
        c.execute("""SELECT
                       pl.account,
                       pl.hostmask,
                       pe.notice,
                       pe.simple,
                       pe.deadchat,
                       pe.pingif,
                       pe.stasis_amount,
                       pe.stasis_expires,
                       COALESCE(at.flags, a.flags)
                     FROM person pe
                     JOIN person_player pp
                       ON pp.person = pe.id
                     JOIN player pl
                       ON pl.id = pp.player
                     LEFT JOIN access a
                       ON a.person = pe.id
                     LEFT JOIN access_template at
                       ON at.id = a.template
                     WHERE pl.active = 1""")

        var.SIMPLE_NOTIFY = set()  # cloaks of people who !simple, who don't want detailed instructions
        var.SIMPLE_NOTIFY_ACCS = set() # same as above, except accounts. takes precedence
        var.PREFER_NOTICE = set()  # cloaks of people who !notice, who want everything /notice'd
        var.PREFER_NOTICE_ACCS = set() # Same as above, except accounts. takes precedence
        var.STASISED = defaultdict(int)
        var.STASISED_ACCS = defaultdict(int)
        var.PING_IF_PREFS = {}
        var.PING_IF_PREFS_ACCS = {}
        var.PING_IF_NUMS = defaultdict(set)
        var.PING_IF_NUMS_ACCS = defaultdict(set)
        var.DEADCHAT_PREFS = set()
        var.DEADCHAT_PREFS_ACCS = set()
        var.FLAGS = defaultdict(str)
        var.FLAGS_ACCS = defaultdict(str)
        var.DENY = defaultdict(set)
        var.DENY_ACCS = defaultdict(set)

        for acc, host, notice, simple, dc, pi, stasis, stasisexp, flags in c:
            if acc is not None:
                if simple == 1:
                    var.SIMPLE_NOTIFY_ACCS.add(acc)
                if notice == 1:
                    var.PREFER_NOTICE_ACCS.add(acc)
                if stasis > 0:
                    var.STASISED_ACCS[acc] = stasis
                if pi is not None and pi > 0:
                    var.PING_IF_PREFS_ACCS[acc] = pi
                    var.PING_IF_NUMS_ACCS[pi].add(acc)
                if dc == 1:
                    var.DEADCHAT_PREFS_ACCS.add(acc)
                if flags:
                    var.FLAGS_ACCS[acc] = flags
            elif host is not None:
                if simple == 1:
                    var.SIMPLE_NOTIFY.add(host)
                if notice == 1:
                    var.PREFER_NOTICE.add(host)
                if stasis > 0:
                    var.STASISED[host] = stasis
                if pi is not None and pi > 0:
                    var.PING_IF_PREFS[host] = pi
                    var.PING_IF_NUMS[pi].add(host)
                if dc == 1:
                    var.DEADCHAT_PREFS.add(host)
                if flags:
                    var.FLAGS[host] = flags

        c.execute("""SELECT
                       pl.account,
                       pl.hostmask,
                       ws.data
                     FROM warning w
                     JOIN warning_sanction ws
                       ON ws.warning = w.id
                     JOIN person pe
                       ON pe.id = w.target
                     JOIN person_player pp
                       ON pp.person = pe.id
                     JOIN player pl
                       ON pl.id = pp.player
                     WHERE
                       ws.sanction = 'deny command'
                       AND w.deleted = 0
                       AND (
                         w.expires IS NULL
                         OR w.expires > datetime('now')
                       )""")
        for acc, host, command in c:
            if acc is not None:
                var.DENY_ACCS[acc].add(command)
            if host is not None:
                var.DENY[host].add(command)

def decrement_stasis(acc=None, hostmask=None):
    peid, plid = _get_ids(acc, hostmask)
    if (acc is not None or hostmask is not None) and peid is None:
        return
    sql = "UPDATE person SET stasis_amount = MAX(0, stasis_amount - 1)"
    params = ()
    if peid is not None:
        sql += " WHERE id = ?"
        params = (peid,)

    with conn:
        c = conn.cursor()
        c.execute(sql, params)

def decrease_stasis(newamt, acc=None, hostmask=None):
    peid, plid = _get_ids(acc, hostmask)
    if peid is None:
        return
    if newamt < 0:
        newamt = 0

    with conn:
        c = conn.cursor()
        c.execute("""UPDATE person
                     SET stasis_amount = MIN(stasis_amount, ?)
                     WHERE id = ?""", (newamt, peid))

def expire_stasis():
    with conn:
        c = conn.cursor()
        c.execute("""UPDATE person
                     SET
                       stasis_amount = 0,
                       stasis_expires = NULL
                     WHERE
                       stasis_expires IS NOT NULL
                       AND stasis_expires <= datetime('now')""")

def get_template(name):
    c = conn.cursor()
    c.execute("SELECT id, flags FROM access_template WHERE name = ?", (name,))
    row = c.fetchone()
    if row is None:
        return (None, set())
    return (row[0], row[1])

def get_templates():
    c = conn.cursor()
    c.execute("SELECT name, flags FROM access_template ORDER BY name ASC")
    tpls = []
    for name, flags in c:
        tpls.append((name, flags))
    return tpls

def update_template(name, flags):
    with conn:
        tid, _ = get_template(name)
        c = conn.cursor()
        if tid is None:
            c.execute("INSERT INTO access_template (name, flags) VALUES (?, ?)", (name, flags))
        else:
            c.execute("UPDATE access_template SET flags = ? WHERE id = ?", (flags, tid))

def delete_template(name):
    with conn:
        tid, _ = get_template(name)
        if tid is not None:
            c = conn.cursor()
            c.execute("DELETE FROM access WHERE template = ?", (tid,))
            c.execute("DELETE FROM template WHERE id = ?", (tid,))

def set_access(acc, hostmask, flags=None, tid=None):
    peid, plid = _get_ids(acc, hostmask)
    if peid is None:
        return
    with conn:
        c = conn.cursor()
        if flags is None and tid is None:
            c.execute("DELETE FROM access WHERE person = ?", (peid,))
        elif tid is not None:
            c.execute("""INSERT OR REPLACE INTO access
                         (person, template, flags)
                         VALUES (?, ?, NULL)""", (peid, tid))
        else:
            c.execute("""INSERT OR REPLACE INTO access
                         (person, template, flags)
                         VALUES (?, NULL, ?)""", (peid, flags))

def toggle_simple(acc, hostmask):
    _toggle_thing("simple", acc, hostmask)

def toggle_notice(acc, hostmask):
    _toggle_thing("notice", acc, hostmask)

def toggle_deadchat(acc, hostmask):
    _toggle_thing("deadchat", acc, hostmask)

def set_pingif(val, acc, hostmask):
    _set_thing("pingif", val, acc, hostmask, raw=False)

def add_game(mode, size, started, finished, winner, players, options):
    """ Adds a game record to the database.

    mode: Game mode (string)
    size: Game size on start (int)
    started: Time when game started (timestamp)
    finished: Time when game ended (timestamp)
    winner: Winning team (string)
    players: List of players (sequence of dict, described below)
    options: Game options (role reveal, stats type, etc., freeform dict)

    Players dict format:
    {
        nick: "Nickname"
        account: "Account name" (or None, "*" is converted to None)
        ident: "Ident"
        host: "Host"
        role: "role name"
        templates: ["template names", ...]
        special: ["special qualities", ... (lover, entranced, etc.)]
        won: True/False
        iwon: True/False
        dced: True/False
    }
    """

    if mode == "roles":
        # Do not record stats for games with custom roles
        return

    # Normalize players dict
    for p in players:
        if p["account"] == "*":
            p["account"] = None
        p["hostmask"] = "{0}!{1}@{2}".format(p["nick"], p["ident"], p["host"])
        c = conn.cursor()
        p["personid"], p["playerid"] = _get_ids(p["account"], p["hostmask"], add=True)
    with conn:
        c = conn.cursor()
        if winner.startswith("@"):
            # fool won, convert the nick portion into a player id
            for p in players:
                if p["nick"] == winner[1:]:
                    winner = "@" + p["playerid"]
                    break
            else:
                # invalid winner? We can't find the fool's nick in the player list
                # maybe raise an exception here instead of silently failing
                return

        c.execute("""INSERT INTO game (gamemode, options, started, finished, gamesize, winner)
                     VALUES (?, ?, ?, ?, ?, ?)""", (mode, json.dumps(options), started, finished, size, winner))
        gameid = c.lastrowid
        for p in players:
            c.execute("""INSERT INTO game_player (game, player, team_win, indiv_win, dced)
                         VALUES (?, ?, ?, ?, ?)""", (gameid, p["playerid"], p["won"], p["iwon"], p["dced"]))
            gpid = c.lastrowid
            c.execute("""INSERT INTO game_player_role (game_player, role, special)
                         VALUES (?, ?, 0)""", (gpid, p["role"]))
            for tpl in p["templates"]:
                c.execute("""INSERT INTO game_player_role (game_player, role, special)
                             VALUES (?, ?, 0)""", (gpid, tpl))
            for sq in p["special"]:
                c.execute("""INSERT INTO game_player_role (game_player, role, special)
                             VALUES (?, ?, 1)""", (gpid, sq))

def get_player_stats(acc, hostmask, role):
    peid, plid = _get_ids(acc, hostmask)
    if not _total_games(peid):
        return "\u0002{0}\u0002 has not played any games.".format(acc if acc and acc != "*" else hostmask)
    c = conn.cursor()
    c.execute("""SELECT
                   gpr.role AS role,
                   SUM(gp.team_win) AS team,
                   SUM(gp.indiv_win) AS indiv,
                   COUNT(1) AS total
                 FROM person pe
                 JOIN person_player pmap
                   ON pmap.person = pe.id
                 JOIN game_player gp
                   ON gp.player = pmap.player
                 JOIN game_player_role gpr
                   ON gpr.game_player = gp.id
                   AND gpr.role = ?
                 WHERE pe.id = ?
                 GROUP BY role""", (role, peid))
    row = c.fetchone()
    name = _get_display_name(peid)
    if row:
        msg = "\u0002{0}\u0002 as \u0002{1}\u0002 | Team wins: {2} (%d%%), Individual wins: {3} (%d%%), Total games: {4}.".format(name, *row)
        return msg % (round(row[1]/row[3] * 100), round(row[2]/row[3] * 100))
    return "No stats for \u0002{0}\u0002 as \u0002{1}\u0002.".format(name, role)

def get_player_totals(acc, hostmask):
    peid, plid = _get_ids(acc, hostmask)
    total_games = _total_games(peid)
    if not total_games:
        return "\u0002{0}\u0002 has not played any games.".format(acc if acc and acc != "*" else hostmask)
    c = conn.cursor()
    c.execute("""SELECT
                   gpr.role AS role,
                   COUNT(1) AS total
                 FROM person pe
                 JOIN person_player pmap
                   ON pmap.person = pe.id
                 JOIN game_player gp
                   ON gp.player = pmap.player
                 JOIN game_player_role gpr
                   ON gpr.game_player = gp.id
                 WHERE pe.id = ?
                 GROUP BY role""", (peid,))
    tmp = {}
    totals = []
    for row in c:
        tmp[row[0]] = row[1]
    order = var.role_order()
    name = _get_display_name(peid)
    #ordered role stats
    totals = ["\u0002{0}\u0002: {1}".format(r, tmp[r]) for r in order if r in tmp]
    #lover or any other special stats
    totals += ["\u0002{0}\u0002: {1}".format(r, t) for r, t in tmp.items() if r not in order]
    return "\u0002{0}\u0002's totals | \u0002{1}\u0002 games | {2}".format(name, total_games, var.break_long_message(totals, ", "))

def get_game_stats(mode, size):
    c = conn.cursor()
    c.execute("SELECT COUNT(1) FROM game WHERE gamemode = ? AND gamesize = ?", (mode, size))
    total_games = c.fetchone()[0]
    if not total_games:
        return "No stats for \u0002{0}\u0002 player games.".format(size)
    c.execute("""SELECT
                   CASE substr(winner, 1, 1)
                     WHEN '@' THEN 'fools'
                     ELSE winner END AS team,
                   COUNT(1) AS games,
                   CASE winner
                     WHEN 'villagers' THEN 0
                     WHEN 'wolves' THEN 1
                     ELSE 2 END AS ord
                 FROM game
                 WHERE
                   gamemode = ?
                   AND gamesize = ?
                   AND winner IS NOT NULL
                 GROUP BY team
                 ORDER BY ord ASC, team ASC""", (mode, size))
    msg = "\u0002{0}\u0002 player games | {1}"
    bits = []
    for row in c:
        bits.append("%s wins: %d (%d%%)" % (var.singular(row[0]), row[1], round(row[1]/total_games * 100)))
    bits.append("total games: {0}".format(total_games))
    return msg.format(size, ", ".join(bits))

def get_game_totals(mode):
    c = conn.cursor()
    c.execute("SELECT COUNT(1) FROM game WHERE gamemode = ?", (mode,))
    total_games = c.fetchone()[0]
    if not total_games:
        return "No games have been played in the {0} game mode.".format(mode)
    c.execute("""SELECT
                   gamesize,
                   COUNT(1) AS games
                 FROM game
                 WHERE gamemode = ?
                 GROUP BY gamesize
                 ORDER BY gamesize ASC""", (mode,))
    totals = []
    for row in c:
        totals.append("\u0002{0}p\u0002: {1}".format(*row))
    return "Total games ({0}) | {1}".format(total_games, ", ".join(totals))

def get_warning_points(acc, hostmask):
    peid, plid = _get_ids(acc, hostmask)
    c = conn.cursor()
    c.execute("""SELECT COALESCE(SUM(amount), 0)
                 FROM warning
                 WHERE
                   target = ?
                   AND deleted = 0
                   AND (
                     expires IS NULL
                     OR expires > datetime('now')
                   )""", (peid,))
    row = c.fetchone()
    return row[0]

def has_unacknowledged_warnings(acc, hostmask):
    peid, plid = _get_ids(acc, hostmask)
    if peid is None:
        return False
    c = conn.cursor()
    c.execute("""SELECT MIN(acknowledged)
                 FROM warning
                 WHERE
                   target = ?
                   AND deleted = 0
                   AND (
                     expires IS NULL
                     OR expires > datetime('now')
                   )""", (peid,))
    row = c.fetchone()
    return not bool(row[0])

def list_all_warnings(list_all=False, skip=0, show=0):
    c = conn.cursor()
    sql = """SELECT
               warning.id,
               COALESCE(plt.account, plt.hostmask) AS target,
               COALESCE(pls.account, pls.hostmask, ?) AS sender,
               warning.amount,
               warning.issued,
               warning.expires,
               CASE WHEN warning.expires IS NULL OR warning.expires > datetime('now')
                    THEN 0 ELSE 1 END AS expired,
               warning.acknowledged,
               warning.deleted,
               warning.reason
             FROM warning
             JOIN person pet
               ON pet.id = warning.target
             JOIN player plt
               ON plt.id = pet.primary_player
             LEFT JOIN person pes
               ON pes.id = warning.sender
             LEFT JOIN player pls
               ON pls.id = pes.primary_player
             """
    if not list_all:
        sql += """WHERE
                    deleted = 0
                    AND (
                      expires IS NULL
                      OR expires > datetime('now')
                    )
                """
    sql += "ORDER BY warning.issued DESC\n"
    if show > 0:
        sql += "LIMIT {0} OFFSET {1}".format(show, skip)

    c.execute(sql, (botconfig.NICK,))
    warnings = []
    for row in c:
        warnings.append({"id": row[0],
                         "target": row[1],
                         "sender": row[2],
                         "amount": row[3],
                         "issued": row[4],
                         "expires": row[5],
                         "expired": row[6],
                         "ack": row[7],
                         "deleted": row[8],
                         "reason": row[9]})
    return warnings

def list_warnings(acc, hostmask, expired=False, deleted=False, skip=0, show=0):
    peid, plid = _get_ids(acc, hostmask)
    c = conn.cursor()
    sql = """SELECT
               warning.id,
               COALESCE(plt.account, plt.hostmask) AS target,
               COALESCE(pls.account, pls.hostmask, ?) AS sender,
               warning.amount,
               warning.issued,
               warning.expires,
               CASE WHEN warning.expires IS NULL OR warning.expires > datetime('now')
                    THEN 0 ELSE 1 END AS expired,
               warning.acknowledged,
               warning.deleted,
               warning.reason
             FROM warning
             JOIN person pet
               ON pet.id = warning.target
             JOIN player plt
               ON plt.id = pet.primary_player
             LEFT JOIN person pes
               ON pes.id = warning.sender
             LEFT JOIN player pls
               ON pls.id = pes.primary_player
             WHERE
               warning.target = ?
             """
    if not deleted:
        sql += " AND deleted = 0"
    if not expired:
        sql += """ AND (
                      expires IS NULL
                      OR expires > datetime('now')
                    )"""
    sql += " ORDER BY warning.issued DESC"
    if show > 0:
        sql += " LIMIT {0} OFFSET {1}".format(show, skip)

    c.execute(sql, (botconfig.NICK, peid))
    warnings = []
    for row in c:
        warnings.append({"id": row[0],
                         "target": row[1],
                         "sender": row[2],
                         "amount": row[3],
                         "issued": row[4],
                         "expires": row[5],
                         "expired": row[6],
                         "ack": row[7],
                         "deleted": row[8],
                         "reason": row[9]})
    return warnings

def get_warning(warn_id, acc=None, hm=None):
    peid, plid = _get_ids(acc, hm)
    c = conn.cursor()
    sql = """SELECT
               warning.id,
               COALESCE(plt.account, plt.hostmask) AS target,
               COALESCE(pls.account, pls.hostmask, ?) AS sender,
               warning.amount,
               warning.issued,
               warning.expires,
               CASE WHEN warning.expires IS NULL OR warning.expires > datetime('now')
                    THEN 0 ELSE 1 END AS expired,
               warning.acknowledged,
               warning.deleted,
               warning.reason,
               warning.notes,
               COALESCE(pld.account, pld.hostmask) AS deleted_by,
               warning.deleted_on
             FROM warning
             JOIN person pet
               ON pet.id = warning.target
             JOIN player plt
               ON plt.id = pet.primary_player
             LEFT JOIN person pes
               ON pes.id = warning.sender
             LEFT JOIN player pls
               ON pls.id = pes.primary_player
             LEFT JOIN person ped
               ON ped.id = warning.deleted_by
             LEFT JOIN player pld
               ON pld.id = ped.primary_player
             WHERE
               warning.id = ?
             """
    params = (botconfig.NICK, warn_id)
    if acc is not None and hm is not None:
        sql += """  AND warning.target = ?
                    AND warning.deleted = 0"""
        params = (botconfig.NICK, warn_id, peid)

    c.execute(sql, params)
    row = c.fetchone()
    if not row:
        return None

    return {"id": row[0],
            "target": row[1],
            "sender": row[2],
            "amount": row[3],
            "issued": row[4],
            "expires": row[5],
            "expired": row[6],
            "ack": row[7],
            "deleted": row[8],
            "reason": row[9],
            "notes": row[10],
            "deleted_by": row[11],
            "deleted_on": row[12],
            "sanctions": get_warning_sanctions(warn_id)}

def get_warning_sanctions(warn_id):
    c = conn.cursor()
    c.execute("SELECT sanction, data FROM warning_sanction WHERE warning=?", (warn_id,))
    sanctions = {}
    for sanc, data in c:
        if sanc == "stasis":
            sanctions["stasis"] = int(data)
        elif sanc == "deny command":
            if "deny" not in sanctions:
                sanctions["deny"] = set()
            sanctions["deny"].add(data)

    return sanctions

def add_warning(tacc, thm, sacc, shm, amount, reason, notes, expires, need_ack):
    teid, tlid = _get_ids(tacc, thm)
    seid, slid = _get_ids(sacc, shm)
    ack = 0 if need_ack else 1
    with conn:
        c = conn.cursor()
        c.execute("""INSERT INTO warning
                     (
                     target, sender, amount,
                     issued, expires,
                     reason, notes,
                     acknowledged
                     )
                     VALUES
                     (
                       ?, ?, ?,
                       datetime('now'), ?,
                       ?, ?,
                       ?
                     )""", (teid, seid, amount, expires, reason, notes, ack))
    return c.lastrowid

def add_warning_sanction(warning, sanction, data):
    with conn:
        c = conn.cursor()
        c.execute("""INSERT INTO warning_sanction
                     (warning, sanction, data)
                     VALUES
                     (?, ?, ?)""", (warning, sanction, data))

        if sanction == "stasis":
            c.execute("SELECT target FROM warning WHERE id = ?", (warning,))
            peid = c.fetchone()[0]
            c.execute("""UPDATE person
                         SET
                           stasis_amount = stasis_amount + ?,
                           stasis_expires = datetime(CASE WHEN stasis_expires IS NULL
                                                            OR stasis_expires <= datetime('now')
                                                          THEN 'now'
                                                          ELSE stasis_expires END,
                                                     '+{0} hours')
                         WHERE id = ?""".format(int(data)), (data, peid))

def del_warning(warning, acc, hm):
    peid, plid = _get_ids(acc, hm)
    with conn:
        c = conn.cursor()
        c.execute("""UPDATE warning
                     SET
                       acknowledged = 1,
                       deleted = 1,
                       deleted_on = datetime('now'),
                       deleted_by = ?
                     WHERE
                       id = ?
                       AND deleted = 0""", (peid, warning))

def set_warning(warning, reason, notes):
    with conn:
        c = conn.cursor()
        c.execute("""UPDATE warning
                     SET reason = ?, notes = ?
                     WHERE id = ?""", (reason, notes, warning))

def acknowledge_warning(warning):
    with conn:
        c = conn.cursor()
        c.execute("UPDATE warning SET acknowledged = 1 WHERE id = ?", (warning,))

def _upgrade():
    # no upgrades yet, once there are some, add methods like _add_table(), _add_column(), etc.
    # that check for the existence of that table/column/whatever and adds/drops/whatevers them
    # as needed. We can't do this purely in SQL because sqlite lacks a scripting-level IF statement.
    pass

def _migrate():
    dn = os.path.dirname(__file__)
    with conn, open(os.path.join(dn, "db.sql"), "rt") as f1, open(os.path.join(dn, "migrate.sql"), "rt") as f2:
        c = conn.cursor()
        #######################################################
        # Step 1: install the new schema (from db.sql script) #
        #######################################################
        c.executescript(f1.read())

        ################################################################
        # Step 2: migrate relevant info from the old schema to the new #
        ################################################################
        c.executescript(f2.read())

        ######################################################################
        # Step 3: Indicate we have updated the schema to the current version #
        ######################################################################
        c.execute("PRAGMA user_version = " + str(SCHEMA_VERSION))

def _install():
    dn = os.path.dirname(__file__)
    with conn, open(os.path.join(dn, "db.sql"), "rt") as f1:
        c = conn.cursor()
        c.executescript(f1.read())
        c.execute("PRAGMA user_version = " + str(SCHEMA_VERSION))

def _get_ids(acc, hostmask, add=False):
    c = conn.cursor()
    if acc == "*":
        acc = None
    if acc is None and hostmask is None:
        return (None, None)
    elif acc is None:
        c.execute("""SELECT pe.id, pl.id
                     FROM player pl
                     JOIN person_player pp
                       ON pp.player = pl.id
                     JOIN person pe
                       ON pe.id = pp.person
                     WHERE
                       pl.account IS NULL
                       AND pl.hostmask = ?
                       AND pl.active = 1""", (hostmask,))
    else:
        hostmask = None
        c.execute("""SELECT pe.id, pl.id
                     FROM player pl
                     JOIN person_player pp
                       ON pp.player = pl.id
                     JOIN person pe
                       ON pe.id = pp.person
                     WHERE
                       pl.account = ?
                       AND pl.hostmask IS NULL
                       AND pl.active = 1""", (acc,))
    row = c.fetchone()
    peid = None
    plid = None
    if row:
        peid, plid = row
    elif add:
        with conn:
            c.execute("INSERT INTO player (account, hostmask) VALUES (?, ?)", (acc, hostmask))
            plid = c.lastrowid
            c.execute("INSERT INTO person (primary_player) VALUES (?)", (plid,))
            peid = c.lastrowid
            c.execute("INSERT INTO person_player (person, player) VALUES (?, ?)", (peid, plid))
    return (peid, plid)

def _get_display_name(peid):
    if peid is None:
        return None
    c = conn.cursor()
    c.execute("""SELECT COALESCE(pp.account, pp.hostmask)
                 FROM person pe
                 JOIN player pp
                   ON pp.id = pe.primary_player
                 WHERE pe.id = ?""", (peid,))
    return c.fetchone()[0]

def _total_games(peid):
    if peid is None:
        return 0
    c = conn.cursor()
    c.execute("""SELECT COUNT(DISTINCT gp.game)
                 FROM person pe
                 JOIN person_player pmap
                   ON pmap.person = pe.id
                 JOIN game_player gp
                   ON gp.player = pmap.player
                 WHERE
                   pe.id = ?""", (peid,))
    # aggregates without GROUP BY always have exactly one row,
    # so no need to check for None here
    return c.fetchone()[0]

def _set_thing(thing, val, acc, hostmask, raw=False):
    with conn:
        c = conn.cursor()
        peid, plid = _get_ids(acc, hostmask, add=True)
        if raw:
            params = (peid,)
        else:
            params = (val, peid)
            val = "?"
        c.execute("""UPDATE person SET {0} = {1} WHERE id = ?""".format(thing, val), params)

def _toggle_thing(thing, acc, hostmask):
    _set_thing(thing, "CASE {0} WHEN 1 THEN 0 ELSE 1 END".format(thing), acc, hostmask, raw=True)

need_install = not os.path.isfile("data.sqlite3")
conn = sqlite3.connect("data.sqlite3")
with conn:
    c = conn.cursor()
    c.execute("PRAGMA foreign_keys = ON")
    if need_install:
        _install()
    c.execute("PRAGMA user_version")
    row = c.fetchone()
    if row[0] == 0:
        # new schema does not exist yet, migrate from old schema
        # NOTE: game stats are NOT migrated to the new schema; the old gamestats table
        # will continue to exist to allow queries against it, however given how horribly
        # inaccurate the stats on it are, it would be a disservice to copy those inaccurate
        # statistics over to the new schema which has the capability of actually being accurate.
        _migrate()
    elif row[0] < SCHEMA_VERSION:
        _upgrade()
    c.close()

del need_install, c
init_vars()

# vim: set expandtab:sw=4:ts=4: