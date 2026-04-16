import { api } from './api.js';

new window.Vue({
    el: '#admin-app',
    delimiters: ['[[', ']]'],
    data() {
        return {
            loading: true,
            auth: {
                user: null,
            },
            users: [],
            dialogVisible: false,
            editingUserId: null,
            form: {
                username: '',
                password: '',
                confirmPassword: '',
                is_locked: false,
            },
        };
    },
    computed: {
        isAdmin() {
            return Boolean(this.auth.user && this.auth.user.username === 'u679c');
        },
    },
    methods: {
        goBack() {
            window.location.href = '/';
        },
        isSelf(user) {
            return Boolean(this.auth.user && user && user.id === this.auth.user.id);
        },
        resetForm() {
            this.form = {
                username: '',
                password: '',
                confirmPassword: '',
                is_locked: false,
            };
            this.editingUserId = null;
        },
        async init() {
            try {
                const me = await api('/api/auth/me');
                this.auth.user = me.user || null;
                if (!this.auth.user || !this.isAdmin) {
                    this.loading = false;
                    return;
                }
                await this.loadUsers();
            } catch (err) {
                this.$message.error(err.message || '加载失败');
            } finally {
                this.loading = false;
            }
        },
        async loadUsers() {
            this.users = await api('/api/admin/users');
        },
        openCreateDialog() {
            this.resetForm();
            this.dialogVisible = true;
        },
        openEditDialog(user) {
            this.editingUserId = user.id;
            this.form = {
                username: user.username || '',
                password: '',
                confirmPassword: '',
                is_locked: Boolean(user.is_locked),
            };
            this.dialogVisible = true;
        },
        async submitUser() {
            const username = (this.form.username || '').trim();
            const password = this.form.password || '';
            const confirmPassword = this.form.confirmPassword || '';
            if (username.length < 3 || username.length > 32) {
                this.$message.error('用户名长度需在 3~32 之间');
                return;
            }

            if (!this.editingUserId) {
                if (password.length < 6) {
                    this.$message.error('密码长度至少 6 位');
                    return;
                }
                if (password !== confirmPassword) {
                    this.$message.error('两次密码输入不一致');
                    return;
                }
            } else if (password || confirmPassword) {
                if (password.length < 6) {
                    this.$message.error('密码长度至少 6 位');
                    return;
                }
                if (password !== confirmPassword) {
                    this.$message.error('两次密码输入不一致');
                    return;
                }
            }

            try {
                if (this.editingUserId) {
                    await api(`/api/admin/users/${this.editingUserId}`, {
                        method: 'PUT',
                        body: JSON.stringify({
                            username,
                            password: password || '',
                            is_locked: Boolean(this.form.is_locked),
                        }),
                    });
                    this.$message.success('用户已更新');
                } else {
                    await api('/api/admin/users', {
                        method: 'POST',
                        body: JSON.stringify({
                            username,
                            password,
                            is_locked: Boolean(this.form.is_locked),
                        }),
                    });
                    this.$message.success('用户已创建');
                }
                this.dialogVisible = false;
                this.resetForm();
                await this.loadUsers();
            } catch (err) {
                this.$message.error(err.message || '保存失败');
            }
        },
        async removeUser(user) {
            if (this.isSelf(user)) {
                this.$message.error('不能删除当前登录管理员账号');
                return;
            }
            if (user && user.is_locked) {
                this.$message.error('该用户已锁定，不可删除');
                return;
            }
            try {
                await this.$confirm(`确定删除用户 ${user.username} 吗？该用户行程也会被删除。`, '提示', {
                    type: 'warning',
                    confirmButtonText: '删除',
                    cancelButtonText: '取消',
                });
                await api(`/api/admin/users/${user.id}`, { method: 'DELETE' });
                this.$message.success('用户已删除');
                await this.loadUsers();
            } catch (err) {
                if (err !== 'cancel') this.$message.error(err.message || '删除失败');
            }
        },
    },
    mounted() {
        this.init();
    },
});
