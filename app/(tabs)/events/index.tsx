import { Link, useFocusEffect } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
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
import { useAuth } from "../../AuthContext";
import { useSettings } from "../../SettingsContext";

interface UserOption {
  id: number;
  username: string;
  email?: string;
}

interface GroupOption {
  id: number;
  name: string;
  members?: UserOption[];
}

interface EventItem {
  id: number;
  title: string;
  description?: string;
  date?: string;
  code?: string;
  auto_approve?: boolean;
  checkin_mode?: "personal_qr" | "event_qr";
  participants?: number[];
  participant_details?: UserOption[];
  groups?: number[];
  group_details?: GroupOption[];
}

const CHECKIN_MODE_OPTIONS = [
  { value: "event_qr" as const, label: "Event QR (self check-in)" },
  { value: "personal_qr" as const, label: "Personal QR (admin scans)" },
];

const combineDateAndTime = (
  current: Date | null,
  mode: "date" | "time",
  incoming: Date
) => {
  if (mode === "date") {
    const next = new Date(incoming);
    if (current) {
      next.setHours(current.getHours(), current.getMinutes(), 0, 0);
    }
    return next;
  }

  const base = current ? new Date(current) : new Date();
  base.setHours(incoming.getHours(), incoming.getMinutes(), 0, 0);
  base.setSeconds(0, 0);
  return base;
};

export default function EventsScreen() {
  const { apiFetch } = useAuth();
  const { timeFormat, dateFormat } = useSettings();
  const locale = timeFormat === "24" ? "en-GB" : "en-US";
  const [events, setEvents] = useState<EventItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [creating, setCreating] = useState(false);

  const [formTitle, setFormTitle] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [checkinMode, setCheckinMode] = useState<"personal_qr" | "event_qr">("event_qr");
  const [eventDateTime, setEventDateTime] = useState<Date | null>(null);
  const [pickerMode, setPickerMode] = useState<"date" | "time" | null>(null);
  const [showPicker, setShowPicker] = useState(false);
  const [iosPickerValue, setIosPickerValue] = useState(() => new Date());

  const [availableUsers, setAvailableUsers] = useState<UserOption[]>([]);
  const [availableGroups, setAvailableGroups] = useState<GroupOption[]>([]);
  const [selectedUserIds, setSelectedUserIds] = useState<number[]>([]);
  const [selectedGroupIds, setSelectedGroupIds] = useState<number[]>([]);
  const [supportLoading, setSupportLoading] = useState(false);
  const [supportError, setSupportError] = useState<string | null>(null);

  const fetchEvents = useCallback(async () => {
    setLoading(true);
    try {
      const response = await apiFetch("/api/events/");
      if (!response.ok) {
        throw new Error(`Unable to load events (${response.status})`);
      }
      const data: EventItem[] = await response.json();
      setEvents(data);
    } catch (error) {
      console.error("Error fetching events", error);
      Alert.alert("Error", "Could not load events right now.");
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    fetchEvents();
  }, [fetchEvents]);

  useFocusEffect(
    useCallback(() => {
      fetchEvents();
    }, [fetchEvents])
  );

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const response = await apiFetch("/api/events/");
      if (!response.ok) {
        throw new Error(`Unable to load events (${response.status})`);
      }
      const data: EventItem[] = await response.json();
      setEvents(data);
    } catch (error) {
      console.error("Error refreshing events", error);
      Alert.alert("Error", "Could not refresh events right now.");
    } finally {
      setRefreshing(false);
    }
  }, [apiFetch]);

  const loadAssociations = useCallback(async () => {
    setSupportLoading(true);
    setSupportError(null);
    try {
      const [usersRes, groupsRes] = await Promise.all([
        apiFetch("/api/users/"),
        apiFetch("/api/groups/"),
      ]);

      if (!usersRes.ok) {
        throw new Error(`Unable to load users (${usersRes.status})`);
      }
      if (!groupsRes.ok) {
        throw new Error(`Unable to load groups (${groupsRes.status})`);
      }

      const [usersData, groupsData]: [UserOption[], GroupOption[]] = await Promise.all([
        usersRes.json(),
        groupsRes.json(),
      ]);

      setAvailableUsers(usersData);
      setAvailableGroups(groupsData);
    } catch (error) {
      console.error("Error loading event associations", error);
      setSupportError("Could not load users and groups. Try again.");
    } finally {
      setSupportLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    if (showCreateModal) {
      loadAssociations();
    }
  }, [showCreateModal, loadAssociations]);

  const listEmptyComponent = useMemo(
    () => (
      <View style={styles.emptyContainer}>
        <Text style={styles.emptyTitle}>No events found</Text>
        <Text style={styles.emptySubtitle}>Pull to refresh to try again.</Text>
      </View>
    ),
    []
  );

  const closeCreateModal = useCallback(() => {
    setShowCreateModal(false);
    setFormTitle("");
    setFormDescription("");
    setEventDateTime(null);
    setPickerMode(null);
    setShowPicker(false);
    setCheckinMode("event_qr");
    setSelectedUserIds([]);
    setSelectedGroupIds([]);
    setSupportError(null);
    setCreating(false);
  }, []);

  const toggleUser = useCallback((id: number) => {
    setSelectedUserIds((prev) =>
      prev.includes(id) ? prev.filter((userId) => userId !== id) : [...prev, id]
    );
  }, []);

  const toggleGroup = useCallback((id: number) => {
    setSelectedGroupIds((prev) =>
      prev.includes(id) ? prev.filter((groupId) => groupId !== id) : [...prev, id]
    );
  }, []);

  const applySelection = useCallback((mode: "date" | "time", value: Date) => {
    setEventDateTime((prev) => {
      const next = combineDateAndTime(prev, mode, value);
      setIosPickerValue(next ?? new Date());
      return next;
    });
  }, []);

  const updateIosPickerValue = useCallback(
    (mode: "date" | "time", value: Date) => {
      setIosPickerValue((prev) => combineDateAndTime(prev, mode, value));
    },
    []
  );

  const openPicker = useCallback(
    (mode: "date" | "time") => {
      if (Platform.OS === "android") {
        DateTimePickerAndroid.open({
          value: eventDateTime ?? new Date(),
          mode,
          is24Hour: timeFormat === "24",
          onChange: (_event, date) => {
            if (!date) return;
            applySelection(mode, date);
          },
        });
        return;
      }
      const base = eventDateTime ?? new Date();
      setIosPickerValue(base);
      setPickerMode(mode);
      setShowPicker(true);
    },
    [eventDateTime, applySelection, timeFormat]
  );

  const formatDateDisplay = useCallback(
    (value?: string) => {
      if (!value) return null;
      const parsed = new Date(value);
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
    },
    [dateFormat, timeFormat]
  );

  const formatDateOnly = useCallback(
    (value?: Date | null) => {
      if (!value) return null;
      const month = `${value.getMonth() + 1}`.padStart(2, "0");
      const day = `${value.getDate()}`.padStart(2, "0");
      const year = `${value.getFullYear()}`;
      return dateFormat === "MDY" ? `${month}/${day}/${year}` : `${day}/${month}/${year}`;
    },
    [dateFormat]
  );

  const formatTimeOnly = useCallback(
    (value?: Date | null) => {
      if (!value) return null;
      return value.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        hour12: timeFormat === "12",
      });
    },
    [timeFormat]
  );

  const handleCreateEvent = useCallback(async () => {
    const title = formTitle.trim();
    const description = formDescription.trim();

    if (!title) {
      Alert.alert("Missing title", "Please enter a title for the event.");
      return;
    }

    const payload: Record<string, unknown> = { title, checkin_mode: checkinMode };
    if (description) payload.description = description;

    if (eventDateTime) {
      payload.date = eventDateTime.toISOString();
    }

    if (selectedUserIds.length) {
      payload.participants = selectedUserIds;
    }
    if (selectedGroupIds.length) {
      payload.groups = selectedGroupIds;
    }

    setCreating(true);
    try {
      const response = await apiFetch("/api/events/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const errorBody = await response.json().catch(() => ({}));
        const message = errorBody?.detail ?? "Unable to create event.";
        throw new Error(message);
      }

      const created: EventItem = await response.json();
      setEvents((prev) => [created, ...prev]);
      closeCreateModal();
      Alert.alert("Success", "Event created successfully.");
    } catch (error: any) {
      console.error("Error creating event", error);
      Alert.alert("Error", error?.message ?? "Failed to create event.");
      setCreating(false);
    }
  }, [
    apiFetch,
    formTitle,
    formDescription,
    checkinMode,
    eventDateTime,
    selectedUserIds,
    selectedGroupIds,
    closeCreateModal,
  ]);

  const renderHeader = useMemo(
    () => (
      <View style={styles.header}>
        <Text style={styles.screenTitle}>Events</Text>
        <TouchableOpacity
          style={styles.addButton}
          onPress={() => setShowCreateModal(true)}
        >
          <Text style={styles.addButtonText}>+ Add</Text>
        </TouchableOpacity>
      </View>
    ),
    []
  );

  const renderMeta = (item: EventItem) => {
    const parts: string[] = [];
    if (item.participant_details?.length) {
      parts.push(`${item.participant_details.length} attendee(s)`);
    } else if (item.participants?.length) {
      parts.push(`${item.participants.length} attendee(s)`);
    }

    if (item.group_details?.length) {
      parts.push(`${item.group_details.length} group(s)`);
    } else if (item.groups?.length) {
      parts.push(`${item.groups.length} group(s)`);
    }

    if (!parts.length) return null;
    return <Text style={styles.meta}>{parts.join(" • ")}</Text>;
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#007AFF" />
          <Text style={styles.loadingText}>Loading events…</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      <FlatList
        contentContainerStyle={events.length ? styles.listContainer : styles.emptyPadding}
        data={events}
        keyExtractor={(item) => String(item.id)}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} />}
        ListEmptyComponent={listEmptyComponent}
        ListHeaderComponent={renderHeader}
        ListHeaderComponentStyle={styles.listHeader}
        renderItem={({ item }) => (
          <Link href={`/events/${item.id}`} asChild>
            <TouchableOpacity style={styles.card}>
              <Text style={styles.title}>{item.title}</Text>
              {formatDateDisplay(item.date) && (
                <Text style={styles.meta}>{formatDateDisplay(item.date)}</Text>
              )}
              {renderMeta(item)}
              {item.description ? (
                <Text style={styles.description}>{item.description}</Text>
              ) : null}
            </TouchableOpacity>
          </Link>
        )}
      />

      <Modal
        transparent
        animationType="slide"
        visible={showCreateModal}
        onRequestClose={closeCreateModal}
      >
        <View style={styles.modalOverlay}>
          <KeyboardAvoidingView
            behavior={Platform.OS === "ios" ? "padding" : undefined}
            style={styles.modalContainer}
          >
            <View style={styles.modalContent}>
              <Text style={styles.modalTitle}>Create Event</Text>
              <ScrollView
                contentContainerStyle={styles.modalScroll}
                keyboardShouldPersistTaps="handled"
              >
                <TextInput
                  style={styles.input}
                  placeholder="Title"
                  value={formTitle}
                  onChangeText={setFormTitle}
                  autoCapitalize="sentences"
                />

                <TextInput
                  style={[styles.input, styles.multilineInput]}
                  placeholder="Description (optional)"
                  value={formDescription}
                  onChangeText={setFormDescription}
                  multiline
                />

                <View style={styles.dateSection}>
                  <Text style={styles.sectionHeading}>Check-in Mode</Text>
                  <Text style={styles.helperText}>
                    How will volunteers check in on the day?
                  </Text>
                  <View style={styles.chipWrap}>
                    {CHECKIN_MODE_OPTIONS.map((option) => (
                      <TouchableOpacity
                        key={option.value}
                        style={[styles.chip, checkinMode === option.value && styles.chipSelected]}
                        onPress={() => setCheckinMode(option.value)}
                      >
                        <Text
                          style={[
                            styles.chipText,
                            checkinMode === option.value && styles.chipTextSelected,
                          ]}
                        >
                          {option.label}
                        </Text>
                      </TouchableOpacity>
                    ))}
                  </View>
                </View>

                <View style={styles.dateSection}>
                  <Text style={styles.sectionHeading}>Date & Time</Text>
                  <View style={styles.selectorGroup}>
                    <TouchableOpacity
                      style={styles.selectorButton}
                      onPress={() => openPicker("date")}
                    >
                      <Text style={styles.selectorText}>
                        {formatDateOnly(eventDateTime) ?? "Select date"}
                      </Text>
                    </TouchableOpacity>

                    <TouchableOpacity
                      style={[styles.selectorButton, !eventDateTime && styles.selectorDisabled]}
                      onPress={() => openPicker("time")}
                      disabled={!eventDateTime && Platform.OS !== "ios"}
                    >
                      <Text
                        style={[
                          styles.selectorText,
                          !eventDateTime && Platform.OS !== "ios" && styles.selectorTextDisabled,
                        ]}
                      >
                        {formatTimeOnly(eventDateTime) ?? "Select time (optional)"}
                      </Text>
                    </TouchableOpacity>

                    {eventDateTime && (
                      <TouchableOpacity
                        style={styles.clearButton}
                        onPress={() => {
                          setEventDateTime(null);
                          setPickerMode(null);
                          setShowPicker(false);
                          setIosPickerValue(new Date());
                        }}
                      >
                        <Text style={styles.clearButtonText}>Clear date</Text>
                      </TouchableOpacity>
                    )}
                    {Platform.OS === "ios" && showPicker && pickerMode && (
                      <View style={styles.iosPickerWrapper}>
                        <DateTimePicker
                          locale={locale}
                          value={iosPickerValue}
                          mode={pickerMode}
                          display="spinner"
                          themeVariant="dark"
                          textColor="#ffffff"
                          onChange={(event: DateTimePickerEvent, date?: Date) => {
                            if (event.type === "dismissed" || !date) return;
                            updateIosPickerValue(pickerMode, date);
                          }}
                          style={styles.iosPicker}
                        />
                        <View style={styles.iosPickerActions}>
                          <TouchableOpacity
                            onPress={() => {
                              if (pickerMode) {
                                applySelection(pickerMode, iosPickerValue);
                              }
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
                </View>

                <Text style={styles.sectionHeading}>Invite Users</Text>
                {supportLoading ? (
                  <ActivityIndicator color="#007AFF" />
                ) : supportError ? (
                  <>
                    <Text style={styles.errorText}>{supportError}</Text>
                    <TouchableOpacity
                      style={styles.retryButton}
                      onPress={loadAssociations}
                    >
                      <Text style={styles.retryButtonText}>Retry</Text>
                    </TouchableOpacity>
                  </>
                ) : availableUsers.length ? (
                  <View style={styles.chipWrap}>
                    {availableUsers.map((user) => {
                      const selected = selectedUserIds.includes(user.id);
                      return (
                        <TouchableOpacity
                          key={user.id}
                          style={[styles.chip, selected && styles.chipSelected]}
                          onPress={() => toggleUser(user.id)}
                        >
                          <Text
                            style={[
                              styles.chipText,
                              selected && styles.chipTextSelected,
                            ]}
                          >
                            {user.username}
                          </Text>
                        </TouchableOpacity>
                      );
                    })}
                  </View>
                ) : (
                  <Text style={styles.helperText}>No users available.</Text>
                )}

                <Text style={styles.sectionHeading}>Include Groups</Text>
                {supportLoading ? (
                  <ActivityIndicator color="#007AFF" />
                ) : supportError ? (
                  <Text style={styles.helperText}>Users/groups failed to load.</Text>
                ) : availableGroups.length ? (
                  <View style={styles.chipWrap}>
                    {availableGroups.map((group) => {
                      const selected = selectedGroupIds.includes(group.id);
                      return (
                        <TouchableOpacity
                          key={group.id}
                          style={[styles.chip, selected && styles.chipSelected]}
                          onPress={() => toggleGroup(group.id)}
                        >
                          <Text
                            style={[
                              styles.chipText,
                              selected && styles.chipTextSelected,
                            ]}
                          >
                            {group.name}
                          </Text>
                        </TouchableOpacity>
                      );
                    })}
                  </View>
                ) : (
                  <Text style={styles.helperText}>No groups available.</Text>
                )}
              </ScrollView>

              <View style={styles.modalButtons}>
                <TouchableOpacity
                  style={[styles.modalButton, styles.cancelButton]}
                  onPress={closeCreateModal}
                  disabled={creating}
                >
                  <Text style={styles.cancelButtonText}>Cancel</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.modalButton, styles.saveButton]}
                  onPress={handleCreateEvent}
                  disabled={creating}
                >
                  <Text style={styles.saveButtonText}>
                    {creating ? "Saving..." : "Save"}
                  </Text>
                </TouchableOpacity>
              </View>
            </View>
          </KeyboardAvoidingView>
        </View>
      </Modal>

    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  listContainer: { padding: 20, gap: 12 },
  emptyPadding: { padding: 20, flexGrow: 1 },
  listHeader: { marginBottom: 12 },
  card: {
    backgroundColor: "#edf2ff",
    padding: 16,
    borderRadius: 12,
    gap: 4,
  },
  title: { fontSize: 18, fontWeight: "600", color: "#0f172a" },
  meta: { fontSize: 14, color: "#475569" },
  description: { fontSize: 14, color: "#1f2937" },
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: 20 },
  loadingText: { marginTop: 12, fontSize: 16, color: "#444" },
  emptyTitle: { fontSize: 18, fontWeight: "600", marginBottom: 8 },
  emptySubtitle: { fontSize: 14, color: "#666" },
  emptyContainer: { flex: 1, alignItems: "center", justifyContent: "center", gap: 8 },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  screenTitle: { fontSize: 24, fontWeight: "700" },
  addButton: {
    backgroundColor: "#007AFF",
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 999,
  },
  addButtonText: { color: "white", fontWeight: "600" },
  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.4)",
    justifyContent: "center",
    padding: 20,
  },
  modalContainer: {
    flex: 1,
    justifyContent: "center",
  },
  modalContent: {
    backgroundColor: "#fff",
    borderRadius: 16,
    padding: 20,
    gap: 16,
    maxHeight: "90%",
  },
  modalTitle: { fontSize: 20, fontWeight: "700", textAlign: "center" },
  modalScroll: { gap: 16 },
  input: {
    borderWidth: 1,
    borderColor: "#d0d7e2",
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
    backgroundColor: "#f8fafc",
    color: "#0f172a",
  },
  multilineInput: { minHeight: 80, textAlignVertical: "top" },
  dateSection: { gap: 12 },
  sectionHeading: { fontSize: 16, fontWeight: "600", color: "#0f172a" },
  helperText: { fontSize: 14, color: "#64748b" },
  chipWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    columnGap: 8,
    rowGap: 8,
  },
  chip: {
    borderRadius: 20,
    paddingVertical: 6,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderColor: "#d0d7e2",
    backgroundColor: "#fff",
  },
  chipSelected: {
    backgroundColor: "#1d4ed8",
    borderColor: "#1d4ed8",
  },
  chipText: { color: "#1f2937", fontSize: 14, fontWeight: "500" },
  chipTextSelected: { color: "#f8fafc" },
  errorText: { fontSize: 14, color: "#b00020" },
  retryButton: { alignSelf: "flex-start" },
  retryButtonText: { color: "#007AFF", fontWeight: "600" },
  modalButtons: {
    flexDirection: "row",
    justifyContent: "flex-end",
    gap: 12,
  },
  modalButton: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 8,
  },
  cancelButton: { backgroundColor: "#e2e8f0" },
  cancelButtonText: { color: "#334155", fontWeight: "500" },
  saveButton: { backgroundColor: "#2563eb" },
  saveButtonText: { color: "white", fontWeight: "600" },
  selectorGroup: { gap: 10 },
  selectorButton: {
    borderWidth: 1,
    borderColor: "#d0d7e2",
    borderRadius: 10,
    paddingVertical: 10,
    paddingHorizontal: 14,
    backgroundColor: "#f8fafc",
  },
  selectorText: { color: "#0f172a", fontWeight: "500" },
  selectorDisabled: { opacity: 0.5 },
  selectorTextDisabled: { color: "#94a3b8" },
  clearButton: { alignSelf: "flex-start" },
  clearButtonText: { color: "#ef4444", fontWeight: "600" },
  iosPickerWrapper: {
    width: "100%",
    backgroundColor: "#0f172a",
    borderRadius: 16,
    marginTop: 12,
    paddingVertical: 12,
    paddingHorizontal: 10,
    gap: 8,
    borderWidth: 1,
    borderColor: "rgba(37,99,235,0.35)",
  },
  iosPicker: { alignSelf: "stretch", backgroundColor: "transparent" },
  iosPickerActions: { alignItems: "flex-end" },
  iosPickerActionText: { color: "#bfdbfe", fontWeight: "600" },
});
