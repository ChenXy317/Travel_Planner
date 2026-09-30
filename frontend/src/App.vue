<script setup lang="ts">
import { ConfigProvider } from "ant-design-vue";
import zhCN from "ant-design-vue/es/locale/zh_CN";
import Chat from "./pages/Chat.vue";

const theme = {
  token: {
    colorPrimary: "#1c4c57",
    colorLink: "#1c4c57",
    colorError: "#a33b32",
    colorSuccess: "#2f6b52",
    borderRadius: 12,
    fontFamily: '"Noto Sans CJK SC", "Noto Sans CJK JP", "PingFang SC", sans-serif',
    colorBgContainer: "#fbf8f3",
    colorBgElevated: "#fbf8f3",
    colorBorder: "#e4d8c8",
    colorText: "#12262c",
    colorTextSecondary: "#6e655b",
    colorTextPlaceholder: "#8a8176",
    controlHeight: 40,
  },
};
</script>

<template>
  <ConfigProvider :locale="zhCN" :theme="theme">
    <Chat />
  </ConfigProvider>
</template>
