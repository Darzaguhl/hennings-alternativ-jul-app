import { Fraunces_600SemiBold, Fraunces_700Bold } from "@expo-google-fonts/fraunces";
import { Inter_400Regular, Inter_500Medium, Inter_600SemiBold, useFonts } from "@expo-google-fonts/inter";
import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { useEffect } from "react";
import { AuthProvider } from "./AuthContext";
import { SettingsProvider } from "./SettingsContext";

SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const [fontsLoaded] = useFonts({
    Fraunces_600SemiBold,
    Fraunces_700Bold,
    Inter_400Regular,
    Inter_500Medium,
    Inter_600SemiBold,
  });

  useEffect(() => {
    if (fontsLoaded) SplashScreen.hideAsync();
  }, [fontsLoaded]);

  if (!fontsLoaded) return null;

  return (
    <SettingsProvider>
      <AuthProvider>
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="index" /> {/* Splash */}
          <Stack.Screen name="login" /> {/* Login */}
          <Stack.Screen name="set-password" /> {/* First-time / lost password setup */}
          <Stack.Screen name="(tabs)" /> {/* Main app (events, groups, etc.) */}
        </Stack>
      </AuthProvider>
    </SettingsProvider>
  );
}
