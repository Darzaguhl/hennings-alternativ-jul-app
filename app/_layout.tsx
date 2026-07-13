import { Stack } from "expo-router";
import { AuthProvider } from "./AuthContext";
import { SettingsProvider } from "./SettingsContext";

export default function RootLayout() {
  return (
    <SettingsProvider>
      <AuthProvider>
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="index" /> {/* Splash */}
          <Stack.Screen name="login" /> {/* Login */}
          <Stack.Screen name="(tabs)" /> {/* Main app (events, groups, etc.) */}
        </Stack>
      </AuthProvider>
    </SettingsProvider>
  );
}
