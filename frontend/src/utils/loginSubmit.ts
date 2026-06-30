export interface LoginSubmitInput {
  username: string;
  password: string;
  isLoading: boolean;
}

/** 返回的 submit 在 loading、空字段或登录错误时返回 false。 */
export function createLoginSubmitter(
  login: (username: string, password: string) => Promise<unknown>,
  onSuccess: () => void,
  onError: (msg: string) => void,
) {
  return async function submit(input: LoginSubmitInput): Promise<boolean> {
    if (input.isLoading) return false;

    if (!input.username.trim() || !input.password) {
      onError('请输入用户名和密码');
      return false;
    }

    try {
      await login(input.username.trim(), input.password);
      onSuccess();
      return true;
    } catch (error) {
      onError(error instanceof Error ? error.message : '登录失败');
      return false;
    }
  };
}
