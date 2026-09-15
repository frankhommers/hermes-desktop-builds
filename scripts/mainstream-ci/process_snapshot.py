"""Parse native ps snapshots without requesting protected-process argv via sysctl."""

def current_user_commands(text, uid):
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split(None, 2)
        if len(fields) != 3 or not fields[0].isdecimal() or not fields[1].isdecimal():
            raise ValueError('Malformed ps process snapshot')
        if int(fields[0]) == uid:
            rows.append((int(fields[1]), fields[2]))
    return rows
