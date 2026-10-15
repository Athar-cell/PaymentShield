/* Same-origin checkout client. No secrets, PINs, OTPs, or card data. */
class PaymentShield {
  async request(path, body, extra = {}) {
    const response = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', ...(extra.headers || {})},
      ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    });
    let result;
    try { result = await response.json(); } catch { throw new Error('The connector returned an unexpected response.'); }
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'The request was rejected. Check the submitted fields.');
    return result;
  }
  createOrder(order) { return this.request('/api/v1/orders', order); }
  getOrder(id) { return this.request(`/api/v1/orders/${encodeURIComponent(id)}`); }
  async checkout(id) {
    const options = await this.request(`/api/v1/orders/${encodeURIComponent(id)}/checkout`, {});
    if (options.provider === 'demo') return {demo: true, options};
    if (!window.Razorpay) {
      await new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://checkout.razorpay.com/v1/checkout.js';
        script.onload = resolve;
        script.onerror = () => reject(new Error('Could not load provider checkout. The order remains pending.'));
        document.head.append(script);
      });
    }
    return new Promise((resolve, reject) => {
      const checkout = new window.Razorpay({...options,
        description: 'PaymentShield test order',
        handler: async data => {
          try { resolve(await this.request(`/api/v1/orders/${encodeURIComponent(id)}/verify`, data)); }
          catch (error) { reject(error); }
        },
        modal: {ondismiss: () => resolve({cancelled: true})},
      });
      checkout.on('payment.failed', () => {
        // A browser failure event is not an authenticated backend payment result.
        reject(new Error('The provider reported a failed attempt. Refresh status for verified confirmation.'));
      });
      checkout.open();
    });
  }
}
window.PaymentShield = PaymentShield;
