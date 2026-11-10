def run(**kwargs):
    try:
        celsius = kwargs.get('celsius')
        if celsius is None:
            return {'success': False, 'error': '缺少必填参数 celsius'}
        if not isinstance(celsius, (int, float)):
            return {'success': False, 'error': '参数 celsius 必须是整数或浮点数'}
        fahrenheit = celsius * 9 / 5 + 32
        return {'success': True, 'fahrenheit': fahrenheit}
    except Exception as e:
        return {'success': False, 'error': str(e)}
