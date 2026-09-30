"""Capture content-free Runtime dependencies before destructive Memory writes."""


def evidence_ids(database, scope, needles):
    needles = {n for n in needles if isinstance(n, str) and 4 <= len(n) <= 2000}
    if not needles:
        return {}
    users = {
        row[0]
        for row in database.execute("SELECT id,input FROM turns")
        if row[1] != "[已遗忘的对话]" and any(n in row[1] for n in needles)
    }
    affected = users | {
        row[0]
        for row in database.execute("SELECT turn_id,payload FROM events")
        if any(n in row[1] for n in needles)
    }
    for row in database.execute(
        "SELECT o.turn_id,o.text,o.fields FROM match_observations o "
        "JOIN matches m ON m.match_id=o.match_id WHERE m.scope=?",
        (scope,),
    ):
        if any(n in row[1] or n in row[2] for n in needles):
            users.add(row[0])
            affected.add(row[0])
    goals = [
        row[0]
        for row in database.execute("SELECT match_id,goal FROM matches WHERE scope=?", (scope,))
        if any(n in row[1] for n in needles)
    ]
    evidence = [
        (row[0], row[1])
        for row in database.execute(
            "SELECT e.match_id,e.goal_hash,e.goal FROM match_goal_evidence e "
            "JOIN matches m ON m.match_id=e.match_id WHERE m.scope=?",
            (scope,),
        )
        if any(n in row[2] for n in needles)
    ]
    return {
        key: sorted(values)
        for key, values in {
            "turn_ids": affected,
            "user_turn_ids": users,
            "match_goal_ids": goals,
            "match_goal_evidence": evidence,
        }.items()
        if values
    }


def erasure_candidates(database, memory):
    """Snapshot candidates, activated only by committed fact/source tombstones.

    A restore may reject its snapshot or retain most facts. Merely preparing this
    map must not erase anything. No body or fact content enters the durable map.
    """
    candidates = []
    seen_sources = set()
    for fact in memory.list_facts():
        matches = evidence_ids(database, memory.scope, {fact["content"]})
        if matches:
            candidates.append({"kind": "fact", "id": fact["id"], **matches})
        for source in memory.sources(fact["id"]):
            if source["source_id"] in seen_sources:
                continue
            seen_sources.add(source["source_id"])
            matches = evidence_ids(database, memory.scope, {source["source_text"]})
            if matches:
                candidates.append({"kind": "source", "id": source["source_id"], **matches})
    return candidates
