import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '../stores/auth'
import ChatView from '../views/ChatView.vue'
import IdentityReviewView from '../views/IdentityReviewView.vue'
import LoginView from '../views/LoginView.vue'
import PassageManualInputView from '../views/PassageManualInputView.vue'
import PassageUploadView from '../views/PassageUploadView.vue'
import ProfileView from '../views/ProfileView.vue'
import RegisterView from '../views/RegisterView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/login', name: 'login', component: LoginView, meta: { guestOnly: true } },
    { path: '/register', name: 'register', component: RegisterView, meta: { guestOnly: true } },
    { path: '/chat', name: 'chat', component: ChatView, meta: { requiresAuth: true } },
    { path: '/profile', name: 'profile', component: ProfileView, meta: { requiresAuth: true } },
    {
      path: '/passages/upload',
      name: 'passage-upload',
      component: PassageUploadView,
      meta: { requiresAuth: true, requiresAdmin: true },
    },
    {
      path: '/passages/manual',
      name: 'passage-manual',
      component: PassageManualInputView,
      meta: { requiresAuth: true, requiresAdmin: true },
    },
    {
      path: '/review/identity',
      name: 'identity-review',
      component: IdentityReviewView,
      meta: { requiresAuth: true },
    },
  ],
})

router.beforeEach(async (to) => {
  const authStore = useAuthStore()
  if (!authStore.user && localStorage.getItem('access_token')) {
    await authStore.restoreSession()
  }

  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    return { name: 'login' }
  }

  if (to.meta.requiresAdmin && !authStore.isAdmin) {
    return { name: 'chat' }
  }

  if (to.name === 'identity-review' && to.query.mode !== 'annotation' && !authStore.isAdmin) {
    return { name: 'identity-review', query: { mode: 'annotation' } }
  }

  if (to.meta.guestOnly && authStore.isAuthenticated) {
    return { name: 'chat' }
  }

  return true
})

export default router
