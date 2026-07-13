import { Link, useLocalSearchParams, useRouter } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Modal,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useAuth } from "../../AuthContext";

interface GroupMember {
  id: number;
  username: string;
  email?: string;
}

interface GroupDetail {
  id: number;
  name: string;
  code?: string;
  created_by?: { id: number; username: string };
  members?: GroupMember[];
}

export default function GroupDetailScreen() {
  const { id } = useLocalSearchParams();
  const groupId = Array.isArray(id) ? id[0] : id;
  const { apiFetch, currentUser } = useAuth();
  const router = useRouter();

  const [group, setGroup] = useState<GroupDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showManageModal, setShowManageModal] = useState(false);
  const [managing, setManaging] = useState(false);
  const [availableUsers, setAvailableUsers] = useState<GroupMember[]>([]);
  const [selectedMemberIds, setSelectedMemberIds] = useState<number[]>([]);
  const [supportLoading, setSupportLoading] = useState(false);
  const [supportError, setSupportError] = useState<string | null>(null);
  const [editGroupName, setEditGroupName] = useState("");

  const loadGroup = useCallback(async () => {
    if (!groupId) {
      setError("Missing group id");
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch(`/api/groups/${groupId}/`);
      if (!response.ok) {
        throw new Error(`Unable to load group (${response.status})`);
      }
      const data: GroupDetail = await response.json();
      setGroup(data);
      setEditGroupName(data.name);
    } catch (err) {
      console.error("Error fetching group", err);
      setError("Could not load this group.");
    } finally {
      setLoading(false);
    }
  }, [apiFetch, groupId]);

  useEffect(() => {
    loadGroup();
  }, [loadGroup]);

  const prepareSelections = useCallback(() => {
    setSelectedMemberIds(group?.members?.map((m) => m.id) ?? []);
    setEditGroupName(group?.name ?? "");
  }, [group]);

  const loadUsers = useCallback(async () => {
    setSupportLoading(true);
    setSupportError(null);
    try {
      const response = await apiFetch("/api/users/");
      if (!response.ok) {
        throw new Error(`Unable to load users (${response.status})`);
      }
      const users: GroupMember[] = await response.json();
      setAvailableUsers(users);
    } catch (err) {
      console.error("Error loading users", err);
      setSupportError("Could not load users. Try again.");
    } finally {
      setSupportLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    if (showManageModal) {
      prepareSelections();
      loadUsers();
    }
  }, [showManageModal, prepareSelections, loadUsers]);

  const handleDelete = useCallback(async () => {
    if (!groupId) return;
    Alert.alert("Delete Group", "Are you sure you want to delete this group?", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: async () => {
          try {
            const response = await apiFetch(`/api/groups/${groupId}/`, {
              method: "DELETE",
            });
            if (!response.ok) {
              const body = await response.json().catch(() => ({}));
              const msg = body?.detail ?? "Failed to delete group.";
              throw new Error(msg);
            }
            Alert.alert("Group deleted");
            router.replace("/groups");
          } catch (error: any) {
            console.error(error);
            Alert.alert("Error", error?.message ?? "Unable to delete group.");
          }
        },
      },
    ]);
  }, [groupId, apiFetch, router]);

  const toggleMember = useCallback((id: number) => {
    setSelectedMemberIds((prev) =>
      prev.includes(id) ? prev.filter((userId) => userId !== id) : [...prev, id]
    );
  }, []);

  const closeManageModal = useCallback(() => {
    setShowManageModal(false);
    setSupportError(null);
    setManaging(false);
  }, []);

  const handleSave = useCallback(async () => {
    if (!groupId) return;
    setManaging(true);
    try {
      const newName = editGroupName.trim() || group?.name || "";
      const response = await apiFetch(`/api/groups/${groupId}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName, member_ids: selectedMemberIds }),
      });

      if (!response.ok) {
        const errorBody = await response.json().catch(() => ({}));
        const message = errorBody?.detail ?? "Unable to update group.";
        throw new Error(message);
      }

      const updated: GroupDetail = await response.json();
      setGroup(updated);
      setEditGroupName(updated.name);
      closeManageModal();
      Alert.alert("Success", "Group updated successfully.");
    } catch (err: any) {
      console.error("Error updating group", err);
      Alert.alert("Error", err?.message ?? "Failed to update group.");
      setManaging(false);
    }
  }, [apiFetch, groupId, selectedMemberIds, closeManageModal, editGroupName, group]);

  if (loading) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#2563eb" />
          <Text style={styles.loadingText}>Loading group…</Text>
        </View>
      </SafeAreaView>
    );
  }

  if (error || !group) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <View style={styles.centered}>
          <Text style={styles.errorText}>{error ?? "Group not found."}</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.headerRow}>
          <Text style={styles.title}>{group.name}</Text>

          {group.created_by?.id === currentUser?.id && (
            <TouchableOpacity style={styles.deleteButton} onPress={handleDelete}>
              <Text style={styles.deleteButtonText}>Delete</Text>
            </TouchableOpacity>
          )}
        </View>

        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Members</Text>
            <View style={styles.headerActions}>
              {group.created_by?.id === currentUser?.id && (
                <TouchableOpacity
                  style={styles.manageButton}
                  onPress={() => setShowManageModal(true)}
                >
                  <Text style={styles.manageButtonText}>Manage</Text>
                </TouchableOpacity>
              )}
            </View>
          </View>

          {group.members?.length ? (
            group.members.map((member) => (
              <View style={styles.memberRow} key={member.id}>
                <Text style={styles.memberName}>{member.username}</Text>
                {member.email ? <Text style={styles.memberEmail}>{member.email}</Text> : null}
              </View>
            ))
          ) : (
            <Text style={styles.placeholder}>No members listed for this group.</Text>
          )}
        </View>

        <Link href="/events" asChild>
          <TouchableOpacity style={styles.cta}>
            <Text style={styles.ctaText}>See Events</Text>
          </TouchableOpacity>
        </Link>

        {group.code && <Text style={styles.codeHint}>ID: {group.code}</Text>}
      </ScrollView>

      <Modal
        transparent
        animationType="slide"
        visible={showManageModal}
        onRequestClose={closeManageModal}
      >
        <View style={styles.modalOverlay}>
          <KeyboardAvoidingView
            behavior={Platform.OS === "ios" ? "padding" : undefined}
            style={styles.modalContainer}
          >
            <View style={styles.modalContent}>
              <Text style={styles.modalTitle}>Update members</Text>
              <ScrollView contentContainerStyle={styles.modalScroll}>
                <TextInput
                  style={styles.input}
                  placeholder="Group name"
                  value={editGroupName}
                  onChangeText={setEditGroupName}
                />
                {supportLoading ? (
                  <ActivityIndicator color="#2563eb" />
                ) : supportError ? (
                  <>
                    <Text style={styles.helperText}>{supportError}</Text>
                    <TouchableOpacity style={styles.retryButton} onPress={loadUsers}>
                      <Text style={styles.retryText}>Retry</Text>
                    </TouchableOpacity>
                  </>
                ) : availableUsers.length ? (
                  <View style={styles.chipWrap}>
                    {availableUsers.map((user) => {
                      const selected = selectedMemberIds.includes(user.id);
                      return (
                        <TouchableOpacity
                          key={user.id}
                          style={[styles.chip, selected && styles.chipSelected]}
                          onPress={() => toggleMember(user.id)}
                        >
                          <Text style={[styles.chipText, selected && styles.chipTextSelected]}>
                            {user.username}
                          </Text>
                        </TouchableOpacity>
                      );
                    })}
                  </View>
                ) : (
                  <Text style={styles.helperText}>No users available.</Text>
                )}
              </ScrollView>

              <View style={styles.modalButtons}>
                <TouchableOpacity
                  style={[styles.modalButton, styles.cancelButton]}
                  onPress={closeManageModal}
                  disabled={managing}
                >
                  <Text style={styles.cancelButtonText}>Cancel</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.modalButton, styles.saveButton]}
                  onPress={handleSave}
                  disabled={managing}
                >
                  <Text style={styles.saveButtonText}>
                    {managing ? "Saving..." : "Save"}
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
  safeArea: { flex: 1, backgroundColor: "#f8fafc" },
  container: { padding: 24, gap: 24 },
  headerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 12,
  },
  title: { fontSize: 26, fontWeight: "700", color: "#0f172a", flexShrink: 1 },
  section: { gap: 12 },
  sectionHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  headerActions: { flexDirection: "row", gap: 8, alignItems: "center" },
  sectionTitle: { fontSize: 20, fontWeight: "600", color: "#0f172a" },
  memberRow: {
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#d6e0f0",
  },
  memberName: { fontSize: 16, fontWeight: "500", color: "#1f2937" },
  memberEmail: { fontSize: 14, color: "#64748b", marginTop: 2 },
  placeholder: { fontSize: 16, color: "#94a3b8" },
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: 20 },
  loadingText: { marginTop: 12, fontSize: 16, color: "#475569" },
  errorText: { fontSize: 16, color: "#b91c1c", textAlign: "center" },
  manageButton: {
    backgroundColor: "#2563eb",
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 999,
  },
  manageButtonText: { color: "white", fontWeight: "600" },
  deleteButton: {
    backgroundColor: "#ef4444",
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
  },
  deleteButtonText: { color: "white", fontWeight: "600" },
  cta: {
    backgroundColor: "#1d4ed8",
    paddingVertical: 14,
    borderRadius: 12,
    alignItems: "center",
  },
  ctaText: { color: "white", fontSize: 16, fontWeight: "600" },
  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(15,23,42,0.45)",
    justifyContent: "center",
    padding: 20,
  },
  modalContainer: {
    flex: 1,
    justifyContent: "center",
  },
  modalContent: {
    backgroundColor: "#fff",
    borderRadius: 18,
    padding: 20,
    gap: 20,
    maxHeight: "90%",
  },
  modalTitle: { fontSize: 20, fontWeight: "700", textAlign: "center", color: "#0f172a" },
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
  retryButton: { alignSelf: "flex-start" },
  retryText: { color: "#2563eb", fontWeight: "600" },
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
  codeHint: {
    marginTop: 16,
    fontSize: 12,
    color: "#94a3b8",
    textTransform: "uppercase",
  },
});
