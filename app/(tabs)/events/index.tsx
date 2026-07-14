import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Modal,
  Platform,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import DateTimePicker, {
  DateTimePickerAndroid,
  DateTimePickerEvent,
} from "@react-native-community/datetimepicker";
import { SafeAreaView } from "react-native-safe-area-context";
import { theme } from "../../../constants/theme";
import { useAuth } from "../../AuthContext";
import { useSettings } from "../../SettingsContext";
import OppgaverSection from "../../../components/OppgaverSection";
import VakterSection from "../../../components/VakterSection";
import PoolSection from "../../../components/PoolSection";
import CheckinSection from "../../../components/CheckinSection";

type CheckinMode = "personal_qr" | "event_qr";

interface EventDetail {
  id: number;
  title: string;
  description?: string;
  date?: string;
  code?: string;
  is_active?: boolean;
  checkin_mode?: CheckinMode;
  created_by?: { id: number; username: string };
  viewer_role?: string | null;
}

const CHECKIN_MODE_OPTIONS: { value: CheckinMode; label: string }[] = [
  { value: "event_qr", label: "Event QR (self check-in)" },
  { value: "personal_qr", label: "Personal QR (admin scans)" },
];

const combineDateAndTime = (current: Date | null, mode: "date" | "time", incoming: Date) => {
  if (mode === "date") {
    const next = new Date(incoming);
    if (current) next.setHours(current.getHours(), current.getMinutes(), 0, 0);
    return next;
  }
  const base = current ? new Date(current) : new Date();
  base.setHours(incoming.getHours(), incoming.getMinutes(), 0, 0);
  base.setSeconds(0, 0);
  return base;
};

/**
 * Multiple events (years) can exist, but only one is ever "active" -- that's
 * the one the public website/app show by default. This screen shows the
 * active event, with a switcher (owner-only) to view/manage past or
 * upcoming years and to activate/deactivate/delete them.
 */
export default function EventScreen() {
  const { apiFetch, currentUser } = useAuth();
  const { timeFormat, dateFormat } = useSettings();

  const [events, setEvents] = useState<EventDetail[]>([]);
  const [selectedEventId, setSelectedEventId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);

  const [showFormModal, setShowFormModal] = useState(false);
  const [formMode, setFormMode] = useState<"edit" | "create">("edit");
  const [saving, setSaving] = useState(false);
  const [formTitle, setFormTitle] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formCheckinMode, setFormCheckinMode] = useState<CheckinMode>("event_qr");
  const [formDateTime, setFormDateTime] = useState<Date | null>(null);
  const [pickerMode, setPickerMode] = useState<"date" | "time" | null>(null);
  const [showPicker, setShowPicker] = useState(false);
  const [iosPickerValue, setIosPickerValue] = useState(() => new Date());

  const loadEvents = useCallback(
    async ({ silent = false }: { silent?: boolean } = {}) => {
      if (!silent) {
        setLoading(true);
        setError(null);
      }
      try {
        const response = await apiFetch("/api/events/");
        if (!response.ok) throw new Error(`Unable to load events (${response.status})`);
        const data: EventDetail[] = await response.json();
        const sorted = [...data].sort((a, b) => b.id - a.id);
        setEvents(sorted);
        setSelectedEventId((prev) => {
          if (prev && sorted.some((e) => e.id === prev)) return prev;
          return sorted.find((e) => e.is_active)?.id ?? sorted[0]?.id ?? null;
        });
      } catch (err) {
        console.error("Error loading events", err);
        if (!silent) setError("Could not load the event.");
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [apiFetch]
  );

  useEffect(() => {
    loadEvents();
  }, [loadEvents]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await loadEvents({ silent: true });
    setRefreshing(false);
  }, [loadEvents]);

  const event = useMemo(
    () => events.find((e) => e.id === selectedEventId) ?? null,
    [events, selectedEventId]
  );

  const formattedDate = useMemo(() => {
    if (!event?.date) return null;
    const parsed = new Date(event.date);
    if (Number.isNaN(parsed.getTime())) return null;
    const month = `${parsed.getMonth() + 1}`.padStart(2, "0");
    const day = `${parsed.getDate()}`.padStart(2, "0");
    const year = `${parsed.getFullYear()}`;
    const datePart = dateFormat === "MDY" ? `${month}/${day}/${year}` : `${day}/${month}/${year}`;
    const timePart = parsed.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      hour12: timeFormat === "12",
    });
    return `${datePart} ${timePart}`;
  }, [event?.date, dateFormat, timeFormat]);

  const formatDateOnly = useCallback(
    (value: Date | null) => {
      if (!value) return null;
      const month = `${value.getMonth() + 1}`.padStart(2, "0");
      const day = `${value.getDate()}`.padStart(2, "0");
      const year = `${value.getFullYear()}`;
      return dateFormat === "MDY" ? `${month}/${day}/${year}` : `${day}/${month}/${year}`;
    },
    [dateFormat]
  );

  const formatTimeOnly = useCallback(
    (value: Date | null) => {
      if (!value) return null;
      return value.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: timeFormat === "12" });
    },
    [timeFormat]
  );

  const openPicker = useCallback(
    (mode: "date" | "time") => {
      if (Platform.OS === "android") {
        DateTimePickerAndroid.open({
          value: formDateTime ?? new Date(),
          mode,
          is24Hour: timeFormat === "24",
          onChange: (_e, date) => {
            if (!date) return;
            setFormDateTime((prev) => combineDateAndTime(prev, mode, date));
          },
        });
        return;
      }
      setIosPickerValue(formDateTime ?? new Date());
      setPickerMode(mode);
      setShowPicker(true);
    },
    [formDateTime, timeFormat]
  );

  const openFormModal = useCallback(
    (mode: "edit" | "create" = "edit") => {
      const editingExisting = mode === "edit" && !!event;
      setFormMode(editingExisting ? "edit" : "create");
      if (editingExisting && event) {
        setFormTitle(event.title);
        setFormDescription(event.description ?? "");
        setFormCheckinMode(event.checkin_mode ?? "event_qr");
        setFormDateTime(event.date ? new Date(event.date) : null);
      } else {
        setFormTitle("");
        setFormDescription("");
        setFormCheckinMode("event_qr");
        setFormDateTime(null);
      }
      setShowFormModal(true);
    },
    [event]
  );

  const closeFormModal = useCallback(() => {
    setShowFormModal(false);
    setShowPicker(false);
    setPickerMode(null);
    setSaving(false);
  }, []);

  const handleSave = useCallback(async () => {
    const title = formTitle.trim();
    if (!title) {
      Alert.alert("Missing title", "Please enter a title for the event.");
      return;
    }

    const payload: Record<string, unknown> = {
      title,
      description: formDescription.trim(),
      checkin_mode: formCheckinMode,
    };
    if (formDateTime) payload.date = formDateTime.toISOString();

    setSaving(true);
    try {
      const editingExisting = formMode === "edit" && !!event;
      const url = editingExisting ? `/api/events/${event!.id}/` : "/api/events/";
      const method = editingExisting ? "PATCH" : "POST";
      const response = await apiFetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body?.detail ?? "Unable to save the event.");
      }
      const saved: EventDetail = await response.json();
      setEvents((prev) => {
        const exists = prev.some((e) => e.id === saved.id);
        return exists ? prev.map((e) => (e.id === saved.id ? saved : e)) : [saved, ...prev];
      });
      setSelectedEventId(saved.id);
      closeFormModal();
    } catch (err: any) {
      console.error("Error saving event", err);
      Alert.alert("Error", err?.message ?? "Failed to save the event.");
      setSaving(false);
    }
  }, [apiFetch, event, formMode, formTitle, formDescription, formCheckinMode, formDateTime, closeFormModal]);

  const handleDelete = useCallback(() => {
    if (!event) return;
    Alert.alert(
      "Delete Event",
      `Are you sure you want to delete "${event.title}"? This permanently removes all its vakter and check-in history too. This cannot be undone.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete",
          style: "destructive",
          onPress: async () => {
            try {
              const response = await apiFetch(`/api/events/${event.id}/`, { method: "DELETE" });
              if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(body?.detail ?? "Failed to delete event.");
              }
              setEvents((prev) => prev.filter((e) => e.id !== event.id));
              setSelectedEventId((prev) => (prev === event.id ? null : prev));
            } catch (err: any) {
              console.error(err);
              Alert.alert("Error", err?.message ?? "Unable to delete event.");
            }
          },
        },
      ]
    );
  }, [apiFetch, event]);

  const handleActivate = useCallback(async () => {
    if (!event) return;
    setSwitching(true);
    try {
      const response = await apiFetch(`/api/events/${event.id}/activate/`, { method: "POST" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body?.detail ?? "Unable to activate this event.");
      }
      await loadEvents({ silent: true });
    } catch (err: any) {
      console.error("Error activating event", err);
      Alert.alert("Error", err?.message ?? "Failed to activate event.");
    } finally {
      setSwitching(false);
    }
  }, [apiFetch, event, loadEvents]);

  const handleDeactivate = useCallback(async () => {
    if (!event) return;
    setSwitching(true);
    try {
      const response = await apiFetch(`/api/events/${event.id}/deactivate/`, { method: "POST" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body?.detail ?? "Unable to deactivate this event.");
      }
      await loadEvents({ silent: true });
    } catch (err: any) {
      console.error("Error deactivating event", err);
      Alert.alert("Error", err?.message ?? "Failed to deactivate event.");
    } finally {
      setSwitching(false);
    }
  }, [apiFetch, event, loadEvents]);

  const isOwner = event?.viewer_role === "owner";
  const isAdmin = isOwner || event?.viewer_role === "admin";
  const isCheckinStaff = isAdmin || event?.viewer_role === "checkin_staff";

  const eventYearLabel = useCallback((candidate: EventDetail) => {
    if (candidate.date) {
      const parsed = new Date(candidate.date);
      if (!Number.isNaN(parsed.getTime())) return `${parsed.getFullYear()}`;
    }
    return candidate.title;
  }, []);

  const formModal = (
    <Modal transparent animationType="slide" visible={showFormModal} onRequestClose={closeFormModal}>
      <View style={styles.modalOverlay}>
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={styles.modalContainer}>
          <View style={styles.modalContent}>
            <Text style={styles.modalTitle}>
              {formMode === "edit" && event ? "Update event details" : "Create a new event"}
            </Text>
            <ScrollView contentContainerStyle={styles.modalScroll}>
              <TextInput style={styles.input} placeholder="Title" value={formTitle} onChangeText={setFormTitle} />

              <Text style={styles.modalSectionHeading}>Date &amp; Time</Text>
              <View style={styles.selectorGroup}>
                <TouchableOpacity style={styles.selectorButton} onPress={() => openPicker("date")}>
                  <Text style={styles.selectorText}>{formatDateOnly(formDateTime) ?? "Select date"}</Text>
                </TouchableOpacity>
                <TouchableOpacity style={styles.selectorButton} onPress={() => openPicker("time")}>
                  <Text style={styles.selectorText}>{formatTimeOnly(formDateTime) ?? "Select time (optional)"}</Text>
                </TouchableOpacity>
                {formDateTime && (
                  <TouchableOpacity style={styles.clearButton} onPress={() => setFormDateTime(null)}>
                    <Text style={styles.clearButtonText}>Clear date</Text>
                  </TouchableOpacity>
                )}
                {Platform.OS === "ios" && showPicker && pickerMode && (
                  <View style={styles.iosPickerWrapper}>
                    <DateTimePicker
                      value={iosPickerValue}
                      mode={pickerMode}
                      display="spinner"
                      themeVariant="dark"
                      textColor="#ffffff"
                      onChange={(evt: DateTimePickerEvent, date?: Date) => {
                        if (evt.type === "dismissed" || !date) return;
                        setIosPickerValue(date);
                      }}
                      style={styles.iosPicker}
                    />
                    <View style={styles.iosPickerActions}>
                      <TouchableOpacity
                        onPress={() => {
                          setFormDateTime((prev) => combineDateAndTime(prev, pickerMode, iosPickerValue));
                          setShowPicker(false);
                          setPickerMode(null);
                        }}
                      >
                        <Text style={styles.iosPickerActionText}>Done</Text>
                      </TouchableOpacity>
                    </View>
                  </View>
                )}
              </View>

              <Text style={styles.modalSectionHeading}>Check-in Mode</Text>
              <View style={styles.chipWrap}>
                {CHECKIN_MODE_OPTIONS.map((option) => (
                  <TouchableOpacity
                    key={option.value}
                    style={[styles.chip, formCheckinMode === option.value && styles.chipSelected]}
                    onPress={() => setFormCheckinMode(option.value)}
                  >
                    <Text style={[styles.chipText, formCheckinMode === option.value && styles.chipTextSelected]}>
                      {option.label}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>

              <Text style={styles.modalSectionHeading}>Description</Text>
              <TextInput
                style={[styles.input, styles.multilineInput]}
                multiline
                placeholder="Describe the event"
                value={formDescription}
                onChangeText={setFormDescription}
              />
            </ScrollView>

            <View style={styles.modalButtons}>
              <TouchableOpacity style={[styles.modalButton, styles.cancelButton]} onPress={closeFormModal} disabled={saving}>
                <Text style={styles.cancelButtonText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity style={[styles.modalButton, styles.saveButton]} onPress={handleSave} disabled={saving}>
                <Text style={styles.saveButtonText}>{saving ? "Saving…" : "Save"}</Text>
              </TouchableOpacity>
            </View>
          </View>
        </KeyboardAvoidingView>
      </View>
    </Modal>
  );

  if (loading) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#2e5339" />
          <Text style={styles.loadingText}>Loading…</Text>
        </View>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <View style={styles.centered}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      </SafeAreaView>
    );
  }

  if (!event) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <View style={styles.centered}>
          <Text style={styles.emptyTitle}>No event set up yet</Text>
          <Text style={styles.emptySubtitle}>Create the event to get started.</Text>
          <TouchableOpacity style={styles.addButton} onPress={() => openFormModal("create")}>
            <Text style={styles.addButtonText}>+ Create event</Text>
          </TouchableOpacity>
        </View>
        {formModal}
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      <ScrollView
        contentContainerStyle={styles.container}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} />}
      >
        {(isOwner || events.length > 1) && (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.eventSwitcherRow}>
            {events.map((e) => (
              <TouchableOpacity
                key={e.id}
                style={[styles.eventChip, e.id === selectedEventId && styles.eventChipSelected]}
                onPress={() => setSelectedEventId(e.id)}
              >
                {e.is_active && <View style={styles.eventChipDot} />}
                <Text style={[styles.eventChipText, e.id === selectedEventId && styles.eventChipTextSelected]}>
                  {eventYearLabel(e)}
                </Text>
              </TouchableOpacity>
            ))}
            {isAdmin && (
              <TouchableOpacity style={styles.eventChipAdd} onPress={() => openFormModal("create")}>
                <Text style={styles.eventChipAddText}>+ New</Text>
              </TouchableOpacity>
            )}
          </ScrollView>
        )}

        <View style={styles.headerRow}>
          <View style={styles.titleColumn}>
            <Text style={styles.title}>{event.title}</Text>
            <View style={[styles.statusBadge, event.is_active ? styles.statusBadgeActive : styles.statusBadgeInactive]}>
              <Text style={[styles.statusBadgeText, event.is_active && styles.statusBadgeTextActive]}>
                {event.is_active ? "Active — shown on website & app" : "Inactive"}
              </Text>
            </View>
          </View>
          {isAdmin && (
            <View style={styles.headerActions}>
              {isOwner && (
                <>
                  {event.is_active ? (
                    <TouchableOpacity style={styles.manageButton} onPress={handleDeactivate} disabled={switching}>
                      <Text style={styles.manageButtonText}>Deactivate</Text>
                    </TouchableOpacity>
                  ) : (
                    <TouchableOpacity style={styles.manageButton} onPress={handleActivate} disabled={switching}>
                      <Text style={styles.manageButtonText}>Activate</Text>
                    </TouchableOpacity>
                  )}
                </>
              )}
              <TouchableOpacity style={styles.manageButton} onPress={() => openFormModal("edit")}>
                <Text style={styles.manageButtonText}>Edit</Text>
              </TouchableOpacity>
              {isOwner && (
                <TouchableOpacity style={styles.deleteButton} onPress={handleDelete}>
                  <Text style={styles.deleteButtonText}>Delete</Text>
                </TouchableOpacity>
              )}
            </View>
          )}
        </View>
        {formattedDate && <Text style={styles.meta}>When: {formattedDate}</Text>}
        {event.description ? (
          <Text style={styles.description}>{event.description}</Text>
        ) : (
          <Text style={styles.placeholder}>No description provided for this event.</Text>
        )}

        <CheckinSection
          eventId={event.id}
          eventCode={event.code}
          checkinMode={event.checkin_mode ?? "event_qr"}
          isCheckinStaff={isCheckinStaff}
          onResolved={() => loadEvents({ silent: true })}
        />

        <VakterSection eventId={event.id} isAdmin={isAdmin} />

        <OppgaverSection />

        {isCheckinStaff && <PoolSection eventId={event.id} />}

        {event.code && <Text style={styles.codeHint}>ID: {event.code}</Text>}
      </ScrollView>

      {formModal}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  container: { padding: 20, gap: 16 },
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: 20, gap: 12 },
  loadingText: { fontSize: 16, color: "#444" },
  errorText: { fontSize: 16, color: "#b00020", textAlign: "center" },
  emptyTitle: { fontSize: 20, fontWeight: "700", color: "#0f172a" },
  emptySubtitle: { fontSize: 14, color: "#64748b", textAlign: "center" },
  addButton: { backgroundColor: theme.primary, paddingHorizontal: 18, paddingVertical: 10, borderRadius: 999, marginTop: 8 },
  addButtonText: { color: "white", fontWeight: "600" },
  eventSwitcherRow: { flexDirection: "row", gap: 8, paddingBottom: 4 },
  eventChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderRadius: 20,
    paddingVertical: 6,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderColor: "#d0d7e2",
    backgroundColor: "#fff",
  },
  eventChipSelected: { backgroundColor: theme.primary, borderColor: theme.primary },
  eventChipText: { color: "#1f2937", fontSize: 13, fontWeight: "500" },
  eventChipTextSelected: { color: "#f8fafc" },
  eventChipDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: theme.accent },
  eventChipAdd: {
    borderRadius: 20,
    paddingVertical: 6,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderColor: "#d0d7e2",
    borderStyle: "dashed",
  },
  eventChipAddText: { color: "#475569", fontSize: 13, fontWeight: "600" },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" },
  headerActions: { flexDirection: "row", flexWrap: "wrap", gap: 8, justifyContent: "flex-end" },
  titleColumn: { flex: 1, gap: 6 },
  title: { fontSize: 26, fontWeight: "700", color: "#0f172a" },
  statusBadge: { alignSelf: "flex-start", borderRadius: 999, paddingVertical: 3, paddingHorizontal: 10 },
  statusBadgeActive: { backgroundColor: "#dcfce7" },
  statusBadgeInactive: { backgroundColor: "#f1f5f9" },
  statusBadgeText: { fontSize: 12, fontWeight: "600", color: "#64748b" },
  statusBadgeTextActive: { color: "#166534" },
  meta: { fontSize: 14, color: "#475569" },
  description: { fontSize: 15, color: "#1f2937" },
  placeholder: { fontSize: 14, color: "#94a3b8", fontStyle: "italic" },
  manageButton: { backgroundColor: "#e2e8f0", paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999 },
  manageButtonText: { color: "#334155", fontWeight: "600" },
  deleteButton: { backgroundColor: "#fee2e2", paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999 },
  deleteButtonText: { color: "#b91c1c", fontWeight: "600" },
  codeHint: { fontSize: 12, color: "#94a3b8", marginTop: 8 },
  modalOverlay: { flex: 1, backgroundColor: "rgba(0,0,0,0.4)", justifyContent: "center", padding: 20 },
  modalContainer: { flex: 1, justifyContent: "center" },
  modalContent: { backgroundColor: "#fff", borderRadius: 16, padding: 20, gap: 16, maxHeight: "90%" },
  modalTitle: { fontSize: 20, fontWeight: "700", textAlign: "center" },
  modalScroll: { gap: 14 },
  modalSectionHeading: { fontSize: 15, fontWeight: "600", color: "#0f172a" },
  input: { borderWidth: 1, borderColor: "#d0d7e2", borderRadius: 8, padding: 12, fontSize: 16, backgroundColor: "#f8fafc", color: "#0f172a" },
  multilineInput: { minHeight: 80, textAlignVertical: "top" },
  selectorGroup: { gap: 10 },
  selectorButton: { borderWidth: 1, borderColor: "#d0d7e2", borderRadius: 10, paddingVertical: 10, paddingHorizontal: 14, backgroundColor: "#f8fafc" },
  selectorText: { color: "#0f172a", fontWeight: "500" },
  clearButton: { alignSelf: "flex-start" },
  clearButtonText: { color: "#ef4444", fontWeight: "600" },
  iosPickerWrapper: { width: "100%", backgroundColor: "#0f172a", borderRadius: 16, marginTop: 12, paddingVertical: 12, paddingHorizontal: 10, gap: 8, borderWidth: 1, borderColor: "rgba(201,154,61,0.35)" },
  iosPicker: { alignSelf: "stretch", backgroundColor: "transparent" },
  iosPickerActions: { alignItems: "flex-end" },
  iosPickerActionText: { color: theme.accentLight, fontWeight: "600" },
  chipWrap: { flexDirection: "row", flexWrap: "wrap", columnGap: 8, rowGap: 8 },
  chip: { borderRadius: 20, paddingVertical: 6, paddingHorizontal: 14, borderWidth: 1, borderColor: "#d0d7e2", backgroundColor: "#fff" },
  chipSelected: { backgroundColor: theme.primary, borderColor: theme.primary },
  chipText: { color: "#1f2937", fontSize: 14, fontWeight: "500" },
  chipTextSelected: { color: "#f8fafc" },
  modalButtons: { flexDirection: "row", justifyContent: "flex-end", gap: 12 },
  modalButton: { paddingHorizontal: 16, paddingVertical: 10, borderRadius: 8 },
  cancelButton: { backgroundColor: "#e2e8f0" },
  cancelButtonText: { color: "#334155", fontWeight: "500" },
  saveButton: { backgroundColor: theme.accent },
  saveButtonText: { color: theme.primaryDark, fontWeight: "600" },
});
