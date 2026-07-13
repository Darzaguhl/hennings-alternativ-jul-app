import { useFocusEffect, useRouter } from "expo-router";
import React, { useCallback, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";

import { useAuth } from "../AuthContext";

interface NotificationItem {
  id: number;
  type: "event_invite" | "group_invite" | "event_group_invite";
  message: string;
  is_read: boolean;
  created_at: string;
  event_invite?: { id: number; event: { id: number } };
  group_invite?: { id: number; group: { id: number } };
  event_group_invite?: { id: number; event: { id: number }; group: { id: number } };
}

const decisionLabels: Record<string, { accept: string; decline: string }> = {
  event_invite: { accept: "Accept", decline: "Decline" },
  group_invite: { accept: "Join", decline: "Decline" },
  event_group_invite: { accept: "Accept", decline: "Decline" },
};

export default function NotificationsScreen() {
  const { apiFetch } = useAuth();
  const router = useRouter();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await apiFetch("/api/notifications/");
      if (!response.ok) {
        throw new Error(`Failed to load notifications (${response.status})`);
      }
      const data: NotificationItem[] = await response.json();
      setItems(data);
    } catch (error) {
      console.error(error);
      Alert.alert("Error", "Could not load notifications.");
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const response = await apiFetch("/api/notifications/");
      if (!response.ok) {
        throw new Error(`Failed to load notifications (${response.status})`);
      }
      const data: NotificationItem[] = await response.json();
      setItems(data);
    } catch (error) {
      console.error(error);
      Alert.alert("Error", "Could not refresh notifications.");
    } finally {
      setRefreshing(false);
    }
  }, [apiFetch]);

  const handleNavigate = useCallback(
    (item: NotificationItem) => {
      if (item.type === "event_invite" && item.event_invite?.event?.id) {
        router.push(`/events/${item.event_invite.event.id}`);
      } else if (item.type === "group_invite" && item.group_invite?.group?.id) {
        router.push(`/groups/${item.group_invite.group.id}`);
      } else if (item.type === "event_group_invite" && item.event_group_invite?.event?.id) {
        router.push(`/events/${item.event_group_invite.event.id}`);
      }
    },
    [router]
  );

  const handleDecision = useCallback(
    async (item: NotificationItem, decision: "accepted" | "declined") => {
      try {
        let endpoint = "";
        if (item.type === "event_invite" && item.event_invite) {
          endpoint = `/api/event-invites/${item.event_invite.id}/respond/`;
        } else if (item.type === "group_invite" && item.group_invite) {
          endpoint = `/api/group-invites/${item.group_invite.id}/respond/`;
        } else if (item.type === "event_group_invite" && item.event_group_invite) {
          endpoint = `/api/event-group-invites/${item.event_group_invite.id}/respond/`;
        }

        if (!endpoint) return;

        const response = await apiFetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision }),
        });

        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          const msg = body?.detail ?? "Failed to update invite.";
          throw new Error(msg);
        }

        await onRefresh();
      } catch (error: any) {
        console.error(error);
        Alert.alert("Error", error?.message ?? "Something went wrong");
      }
    },
    [apiFetch, onRefresh]
  );

  const renderItem = ({ item }: { item: NotificationItem }) => {
    const decisions = decisionLabels[item.type];
    return (
      <View style={[styles.card, item.is_read && styles.cardRead]}>
        <TouchableOpacity onPress={() => handleNavigate(item)}>
          <Text style={styles.message}>{item.message}</Text>
          <Text style={styles.timestamp}>{new Date(item.created_at).toLocaleString()}</Text>
        </TouchableOpacity>

        {item.type !== "event_group_invite" || item.event_group_invite ? (
          <View style={styles.actions}>
            <TouchableOpacity
              style={[styles.button, styles.acceptButton]}
              onPress={() => handleDecision(item, "accepted")}
            >
              <Text style={styles.buttonText}>{decisions.accept}</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.button, styles.declineButton]}
              onPress={() => handleDecision(item, "declined")}
            >
              <Text style={styles.buttonText}>{decisions.decline}</Text>
            </TouchableOpacity>
          </View>
        ) : null}
      </View>
    );
  };

  if (loading) {
    return (
      <View style={styles.loaderContainer}>
        <ActivityIndicator size="large" color="#2563eb" />
      </View>
    );
  }

  return (
    <FlatList
      contentContainerStyle={styles.listContainer}
      data={items}
      keyExtractor={(item) => String(item.id)}
      renderItem={renderItem}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      ListEmptyComponent={
        <View style={styles.emptyContainer}>
          <Text style={styles.emptyText}>No notifications right now.</Text>
        </View>
      }
    />
  );
}

const styles = StyleSheet.create({
  loaderContainer: { flex: 1, justifyContent: "center", alignItems: "center" },
  listContainer: { padding: 20, gap: 12 },
  card: {
    backgroundColor: "#0f172a",
    borderRadius: 14,
    padding: 16,
    gap: 12,
    borderWidth: 1,
    borderColor: "rgba(37,99,235,0.4)",
  },
  cardRead: { opacity: 0.7 },
  message: { color: "#f8fafc", fontSize: 16, fontWeight: "600" },
  timestamp: { color: "#94a3b8", fontSize: 13 },
  actions: { flexDirection: "row", gap: 12 },
  button: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: 999,
    alignItems: "center",
  },
  acceptButton: { backgroundColor: "#22c55e" },
  declineButton: { backgroundColor: "#ef4444" },
  buttonText: { color: "white", fontWeight: "600" },
  emptyContainer: { flex: 1, alignItems: "center", justifyContent: "center", paddingTop: 100 },
  emptyText: { color: "#475569" },
});
