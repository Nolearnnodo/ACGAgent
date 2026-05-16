<template>
  <!--
    KeepAlive 让所有页面切换时保留组件实例（不销毁），
    切回页面后 ref 状态、滚动位置、定时器都还在，
    特别是「古籍上传」运行中切走再切回不会丢任务监控。
  -->
  <RouterView v-slot="{ Component }">
    <KeepAlive>
      <component :is="Component" />
    </KeepAlive>
  </RouterView>
</template>
