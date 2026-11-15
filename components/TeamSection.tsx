import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import { theme } from "../constants/theme";
import { useAuth } from "../app/AuthContext";

interface TeamMember {
  id: number;
  username: string;
  email?: string;
  first_name?: string;
  last_name?: string;
}

interface LedShift {
  id: number;
  title: string;
  date: string;
  start_time: string;
  end_time: string;
  is_led_by_viewer?: boolean;
}

interface TeamAssignment {
  id: number;
  user: TeamMember;
  oppgave_slot: { skill_name?: string };
}

interface Commendation {
  id: number;
  user: number;
  shift: number;
}

const memberName = (user: TeamMember) =>
  `${user.first_name ?? ""} ${user.last_name ?? ""}`.trim() || user.email || user.username;

const todayIso = () => {
  const now = new Date();
  return `${now.getFullYear()}-${`${now.getMonth() + 1}`.padStart(2, "0")}-${`${now.getDate()}`.padStart(2, "0")}`;
};

/**
 * A leader's own team, per vakt they lead, with a "Fremhev" button for
 * someone who could take on more responsibility. Renders nothing at all
 * for anyone who doesn't lead a vakt, so it can sit on the event screen
 * for everyone without cluttering non-leaders' view.
 *
 * The flags are internal -- see Commendation on the backend. This screen
 * only ever shows the viewer's own flags (the API scopes them), and the
 * flagged person never gets any indication it happened.
 */
export default function TeamSection({ eventId }: { eventId: number }) {
  const { apiFetch, currentUser } = useAuth();
  const [loading, setLoading] = useState(true);
  const [shifts, setShifts] = useState<LedShift[]>([]);
  const [teams, setTeams] = useState<Record<number, TeamAssignment[]>>({});
  const [flags, setFlags] = useState<Commendation[]>([]);
  const [notingKey, setNotingKey] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const shiftsRes = await apiFetch(`/api/shifts/?event=${eventId}`);
      if (!shiftsRes.ok) throw new Error(`shifts ${shiftsRes.status}`);
      const allShifts: LedShift[] = await shiftsRes.json();
      const led = allShifts
        .filter((s) => s.is_led_by_viewer)
        .sort((a, b) => `${a.date} ${a.start_time}`.localeCompare(`${b.date} ${b.start_time}`));
      setShifts(led);
      if (led.length === 0) return;

      const [flagsRes, ...teamRes] = await Promise.all([
        apiFetch(`/api/commendations/?event=${eventId}`),
        ...led.map((s) => apiFetch(`/api/shifts/${s.id}/assignments/`)),
      ]);
      if (flagsRes.ok) setFlags(await flagsRes.json());
      const next: Record<number, TeamAssignment[]> = {};
      for (let i = 0; i < led.length; i++) {
        next[led[i].id] = teamRes[i].ok ? await teamRes[i].json() : [];
      }
      setTeams(next);
    } catch (err) {
      console.error("Error loading team", err);
      Alert.alert("Feil", "Kunne ikke laste teamet ditt akkurat nå.");
    } finally {
      setLoading(false);
    }
  }, [apiFetch, eventId]);

  useEffect(() => {
    load();
  }, [load]);

  const flagFor = (shiftId: number, userId: number) => flags.find((f) => f.shift === shiftId && f.user === userId);

  const handleFlag = useCallback(
    async (shiftId: number, userId: number) => {
      const key = `${shiftId}-${userId}`;
      setBusyKey(key);
      try {
        const response = await apiFetch("/api/commendations/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ shift: shiftId, user: userId, note: note.trim() }),
        });
        if (!response.ok) {
          const body = await response.json().catch(() => null);
          throw new Error(body?.detail ?? (Array.isArray(body) ? body[0] : null) ?? "Kunne ikke fremheve.");
        }
        const created: Commendation = await response.json();
        setFlags((prev) => [...prev, created]);
        setNotingKey(null);
        setNote("");
      } catch (err: any) {
        Alert.alert("Feil", err?.message ?? "Kunne ikke fremheve.");
      } finally {
        setBusyKey(null);
      }
    },
    [apiFetch, note]
  );

  const handleUnflag = useCallback(
    (flag: Commendation) => {
      Alert.alert("Fjerne fremhevingen?", undefined, [
        { text: "Avbryt", style: "cancel" },
        {
          text: "Fjern",
          style: "destructive",
          onPress: async () => {
            try {
              const response = await apiFetch(`/api/commendations/${flag.id}/`, { method: "DELETE" });
              if (!response.ok && response.status !== 204) throw new Error(`delete ${response.status}`);
              setFlags((prev) => prev.filter((f) => f.id !== flag.id));
            } catch (err) {
              console.error("Error removing commendation", err);
              Alert.alert("Feil", "Kunne ikke fjerne fremhevingen.");
            }
          },
        },
      ]);
    },
    [apiFetch]
  );

  if (loading) {
    return (
      <View style={styles.section}>
        <ActivityIndicator color={theme.primary} />
      </View>
    );
  }
  if (shifts.length === 0) return null;

  const today = todayIso();

  return (
    <View style={styles.section}>
      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>Mitt team</Text>
        <TouchableOpacity onPress={load}>
          <Text style={styles.refreshText}>Oppdater</Text>
        </TouchableOpacity>
      </View>
      <Text style={styles.hint}>
        Fremhev noen som kan ta mer ansvar. Kun admin og du ser dette — personen får ikke vite om det.
      </Text>

      {shifts.map((shift) => {
        const members = (teams[shift.id] ?? []).filter((a) => a.user.id !== currentUser?.id);
        return (
          <View key={shift.id} style={styles.card}>
            <Text style={styles.shiftTitle}>
              {shift.title}
              {shift.date === today ? " · i dag" : ` · ${shift.date}`}
            </Text>
            <Text style={styles.meta}>
              {shift.start_time.slice(0, 5)}–{shift.end_time.slice(0, 5)}
            </Text>

            {members.length === 0 && <Text style={styles.placeholder}>Ingen tildelt ennå.</Text>}

            {members.map((assignment) => {
              const key = `${shift.id}-${assignment.user.id}`;
              const flag = flagFor(shift.id, assignment.user.id);
              return (
                <View key={assignment.id} style={styles.memberBlock}>
                  <View style={styles.memberRow}>
                    <View style={styles.memberInfo}>
                      <Text style={styles.memberName}>{memberName(assignment.user)}</Text>
                      {assignment.oppgave_slot?.skill_name && (
                        <Text style={styles.meta}>{assignment.oppgave_slot.skill_name}</Text>
                      )}
                    </View>
                    {flag ? (
                      <TouchableOpacity style={styles.flaggedButton} onPress={() => handleUnflag(flag)}>
                        <Text style={styles.flaggedButtonText}>★ Fremhevet</Text>
                      </TouchableOpacity>
                    ) : (
                      <TouchableOpacity
                        style={[styles.flagButton, busyKey === key && styles.buttonDisabled]}
                        disabled={busyKey === key}
                        onPress={() => {
                          setNotingKey(notingKey === key ? null : key);
                          setNote("");
                        }}
                      >
                        <Text style={styles.flagButtonText}>Fremhev</Text>
                      </TouchableOpacity>
                    )}
                  </View>

                  {notingKey === key && !flag && (
                    <View style={styles.noteRow}>
                      <TextInput
                        style={styles.noteInput}
                        value={note}
                        onChangeText={setNote}
                        placeholder="Valgfritt: hva gjorde de bra?"
                        maxLength={300}
                      />
                      <TouchableOpacity
                        style={styles.flagButton}
                        onPress={() => handleFlag(shift.id, assignment.user.id)}
                      >
                        <Text style={styles.flagButtonText}>Lagre</Text>
                      </TouchableOpacity>
                    </View>
                  )}
                </View>
              );
            })}
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  section: { gap: 12, marginBottom: 20 },
  sectionHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  sectionTitle: { fontSize: 18, fontWeight: "700", color: "#0f172a" },
  refreshText: { color: theme.primary, fontWeight: "600" },
  hint: { color: "#64748b", fontSize: 13 },
  placeholder: { color: "#64748b", fontSize: 14 },
  card: { backgroundColor: "#f8fafc", borderRadius: 12, padding: 14, gap: 8, borderWidth: 1, borderColor: "#e2e8f0" },
  shiftTitle: { fontSize: 16, fontWeight: "700", color: "#0f172a" },
  meta: { fontSize: 12, color: "#475569" },
  memberBlock: { gap: 8 },
  memberRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: "#fff",
    borderRadius: 10,
    padding: 10,
    borderWidth: 1,
    borderColor: "#e2e8f0",
    gap: 10,
  },
  memberInfo: { flex: 1, gap: 2 },
  memberName: { fontSize: 14, fontWeight: "600", color: "#0f172a" },
  flagButton: { backgroundColor: theme.accent, paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8 },
  flagButtonText: { color: theme.primaryDark, fontWeight: "600" },
  flaggedButton: { backgroundColor: "#dcfce7", paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8 },
  flaggedButtonText: { color: "#166534", fontWeight: "700" },
  noteRow: { flexDirection: "row", gap: 8, alignItems: "center" },
  noteInput: {
    flex: 1,
    backgroundColor: "#fff",
    borderWidth: 1,
    borderColor: "#e2e8f0",
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    fontSize: 14,
  },
  buttonDisabled: { opacity: 0.5 },
});
