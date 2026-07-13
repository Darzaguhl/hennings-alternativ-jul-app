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
import { SafeAreaView } from "react-native-safe-area-context";
import { useAuth } from "../../AuthContext";

interface GroupMember {
  id: number;
  username: string;
  email?: string;
}

interface GroupItem {
  id: number;
  name: string;
  created_by?: { id: number; username: string };
  code?: string;
  members?: GroupMember[];
}

export default function GroupsScreen() {
  const { apiFetch } = useAuth();
  const [groups, setGroups] = useState<GroupItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [groupName, setGroupName] = useState("");
  const [creating, setCreating] = useState(false);

  const [availableUsers, setAvailableUsers] = useState<GroupMember[]>([]);
  const [selectedMemberIds, setSelectedMemberIds] = useState<number[]>([]);
  const [supportLoading, setSupportLoading] = useState(false);
  const [supportError, setSupportError] = useState<string | null>(null);

  const fetchGroups = useCallback(async () => {
    setLoading(true);
    try {
      const response = await apiFetch("/api/groups/");
      if (!response.ok) {
        throw new Error(`Unable to load groups (${response.status})`);
      }
      const data: GroupItem[] = await response.json();
      setGroups(data);
    } catch (error) {
      console.error("Error fetching groups", error);
      Alert.alert("Error", "Could not load groups right now.");
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    fetchGroups();
  }, [fetchGroups]);

  useFocusEffect(
    useCallback(() => {
      fetchGroups();
    }, [fetchGroups])
  );

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const response = await apiFetch("/api/groups/");
      if (!response.ok) {
        throw new Error(`Unable to load groups (${response.status})`);
      }
      const data: GroupItem[] = await response.json();
      setGroups(data);
    } catch (error) {
      console.error("Error refreshing groups", error);
      Alert.alert("Error", "Could not refresh groups right now.");
    } finally {
      setRefreshing(false);
    }
  }, [apiFetch]);

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
    } catch (error) {
      console.error("Error loading users", error);
      setSupportError("Could not load users. Try again.");
    } finally {
      setSupportLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    if (showCreateModal) {
      loadUsers();
    }
  }, [showCreateModal, loadUsers]);

  const listEmptyComponent = useMemo(
    () => (
      <View style={styles.emptyContainer}>
        <Text style={styles.emptyTitle}>No groups found</Text>
        <Text style={styles.emptySubtitle}>Pull to refresh to try again.</Text>
      </View>
    ),
    []
  );

  const closeCreateModal = useCallback(() => {
    setShowCreateModal(false);
    setGroupName("");
    setSelectedMemberIds([]);
    setSupportError(null);
    setCreating(false);
  }, []);

  const toggleMember = useCallback((id: number) => {
    setSelectedMemberIds((prev) =>
      prev.includes(id) ? prev.filter((userId) => userId !== id) : [...prev, id]
    );
  }, []);

  const handleCreateGroup = useCallback(async () => {
    const name = groupName.trim();
    if (!name) {
      Alert.alert("Missing name", "Please enter a group name.");
      return;
    }

    const payload: Record<string, unknown> = { name };
    if (selectedMemberIds.length) {
      payload.member_ids = selectedMemberIds;
    }

    setCreating(true);
    try {
      const response = await apiFetch("/api/groups/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const errorBody = await response.json().catch(() => ({}));
        const message = errorBody?.detail ?? "Unable to create group.";
        throw new Error(message);
      }

      const created: GroupItem = await response.json();
      setGroups((prev) => [created, ...prev]);
      closeCreateModal();
      Alert.alert("Success", "Group created successfully.");
    } catch (error: any) {
      console.error("Error creating group", error);
      Alert.alert("Error", error?.message ?? "Failed to create group.");
      setCreating(false);
    }
  }, [apiFetch, groupName, selectedMemberIds, closeCreateModal]);

  const renderHeader = useMemo(
    () => (
      <View style={styles.header}>
        <Text style={styles.screenTitle}>Groups</Text>
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

  if (loading) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#007AFF" />
          <Text style={styles.loadingText}>Loading groups…</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      <FlatList
        contentContainerStyle={groups.length ? styles.listContainer : styles.emptyPadding}
        data={groups}
        keyExtractor={(item) => String(item.id)}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} />}
        ListEmptyComponent={listEmptyComponent}
        ListHeaderComponent={renderHeader}
        ListHeaderComponentStyle={styles.listHeader}
        renderItem={({ item }) => (
          <Link href={`/groups/${item.id}`} asChild>
            <TouchableOpacity style={styles.card}>
              <Text style={styles.title}>{item.name}</Text>
              {!!item.members?.length && (
                <Text style={styles.meta}>
                  {item.members.length} {item.members.length === 1 ? "member" : "members"}
                </Text>
              )}
            </TouchableOpacity>
          </Link>
        )}
      />

      <Modal
        visible={showCreateModal}
        transparent
        animationType="slide"
        onRequestClose={closeCreateModal}
      >
        <View style={styles.modalOverlay}>
          <KeyboardAvoidingView
            behavior={Platform.OS === "ios" ? "padding" : undefined}
            style={styles.modalContainer}
          >
            <View style={styles.modalContent}>
              <Text style={styles.modalTitle}>Create Group</Text>
              <ScrollView
                contentContainerStyle={styles.modalScroll}
                keyboardShouldPersistTaps="handled"
              >
                <TextInput
                  style={styles.input}
                  placeholder="Group name"
                  value={groupName}
                  onChangeText={setGroupName}
                />

                <Text style={styles.sectionHeading}>Add Members</Text>
                {supportLoading ? (
                  <ActivityIndicator color="#007AFF" />
                ) : supportError ? (
                  <>
                    <Text style={styles.errorText}>{supportError}</Text>
                    <TouchableOpacity style={styles.retryButton} onPress={loadUsers}>
                      <Text style={styles.retryButtonText}>Retry</Text>
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
                  onPress={handleCreateGroup}
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
    backgroundColor: "#f5f5f5",
    padding: 16,
    borderRadius: 12,
    gap: 4,
  },
  title: { fontSize: 18, fontWeight: "600" },
  meta: { fontSize: 14, color: "#666" },
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
    backgroundColor: "white",
    borderRadius: 16,
    padding: 20,
    gap: 16,
    maxHeight: "90%",
  },
  modalTitle: { fontSize: 20, fontWeight: "700", textAlign: "center" },
  modalScroll: { gap: 16 },
  input: {
    borderWidth: 1,
    borderColor: "#d0d0d0",
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
  },
  sectionHeading: { fontSize: 16, fontWeight: "600" },
  helperText: { fontSize: 14, color: "#666" },
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
    borderColor: "#d0d0d0",
    backgroundColor: "white",
  },
  chipSelected: {
    backgroundColor: "#007AFF",
    borderColor: "#007AFF",
  },
  chipText: { color: "#333", fontSize: 14, fontWeight: "500" },
  chipTextSelected: { color: "white" },
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
  cancelButton: { backgroundColor: "#eee" },
  cancelButtonText: { color: "#333", fontWeight: "500" },
  saveButton: { backgroundColor: "#007AFF" },
  saveButtonText: { color: "white", fontWeight: "600" },
});
