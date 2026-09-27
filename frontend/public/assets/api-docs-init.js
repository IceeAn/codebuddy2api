/* 文档初始化使用同源外链；禁止持久化认证信息和访问远程校验器。 */
window.ui = window.SwaggerUIBundle({
  url: document.getElementById('swagger-ui').dataset.schemaUrl,
  dom_id: '#swagger-ui',
  deepLinking: false,
  validatorUrl: null,
  persistAuthorization: false,
  filter: true,
  displayRequestDuration: true,
});
