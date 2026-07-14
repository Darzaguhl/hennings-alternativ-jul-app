import { Ionicons } from "@expo/vector-icons";
import { Tabs, useRouter } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Animated, StyleSheet, TouchableOpacity, View } from "react-native";
import { theme } from "../../constants/theme";
import { useAuth } from "../AuthContext";

export default function TabsLayout() {
  const { token, loading } = useAuth();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const animation = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (!loading && !token) {
      router.replace("/login");
    }
  }, [loading, token, router]);

  useEffect(() => {
    if (!token) {
      setOpen(false);
      animation.setValue(0);
    }
  }, [token, animation]);

  const toggleMenu = () => {
    Animated.timing(animation, {
      toValue: open ? 0 : 1,
      duration: 200,
      useNativeDriver: true,
    }).start(() => setOpen((prev) => !prev));
  };

  const menuItems = [
    { icon: "id-card-outline", route: "/qrcode" },
    { icon: "person-outline", route: "/profile" },
  ];

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color={theme.primary} />
      </View>
    );
  }

  if (!token) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color={theme.primary} />
      </View>
    );
  }

  return (
    <View style={{ flex: 1 }}>
      <Tabs
        initialRouteName="events/index"
        screenOptions={{
          headerShown: false,
          tabBarShowLabel: false,
          tabBarActiveTintColor: theme.primary,
          tabBarInactiveTintColor: theme.textMuted,
        }}
      >
        <Tabs.Screen
          name="events/index"
          options={{
            title: "Alternativ Jul",
            tabBarIcon: ({ color, size }) => (
              <Ionicons name="calendar-outline" size={size} color={color} />
            ),
          }}
        />

        {/* hidden pages */}
        {["qrcode", "profile"].map((s) => (
          <Tabs.Screen key={s} name={s} options={{ href: null }} />
        ))}
      </Tabs>

      {/* Floating Action Button (FAB) */}
      <View style={styles.fabContainer}>
        {open &&
          menuItems.map((item, index) => {
            const angle = (Math.PI / 2 / (menuItems.length - 1)) * index;
            const radius = 120;
            const translateX = animation.interpolate({
              inputRange: [0, 1],
              outputRange: [0, -radius * Math.cos(angle)],
            });
            const translateY = animation.interpolate({
              inputRange: [0, 1],
              outputRange: [0, -radius * Math.sin(angle)],
            });
            const opacity = animation.interpolate({
              inputRange: [0, 1],
              outputRange: [0, 1],
            });

            return (
              <Animated.View
                key={item.route}
                style={[
                  styles.menuItem,
                  { transform: [{ translateX }, { translateY }], opacity },
                ]}
              >
                <TouchableOpacity
                  onPress={() => {
                    toggleMenu();
                    router.push(item.route);
                  }}
                >
                  <Ionicons name={item.icon as any} size={28} color={theme.primary} />
                </TouchableOpacity>
              </Animated.View>
            );
          })}

        <TouchableOpacity style={styles.fab} onPress={toggleMenu}>
          <Ionicons name={open ? "close" : "add"} size={32} color="white" />
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  loadingContainer: { flex: 1, justifyContent: "center", alignItems: "center" },
  fabContainer: { position: "absolute", bottom: 90, right: 10, alignItems: "center" },
  fab: {
    backgroundColor: theme.accent,
    width: 60,
    height: 60,
    borderRadius: 30,
    justifyContent: "center",
    alignItems: "center",
    elevation: 5,
  },
  menuItem: {
    position: "absolute",
    right: 0,
    bottom: 0,
    backgroundColor: "white",
    padding: 10,
    borderRadius: 30,
    elevation: 3,
  },
});
