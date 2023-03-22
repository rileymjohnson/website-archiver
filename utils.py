# Based on this script: https://github.com/mathiasbynens/CSS.escape/blob/master/css.escape.js
def escape_css(css: str) -> str:
    if css == '-':
        return f'\\{css}'

    result = ''

    for i, code_unit in enumerate(map(ord, css)):
        if code_unit == 0x0000:
            result += '\uFFFD'
        elif (
            0x0001 <= code_unit <= 0x001F or \
            code_unit == 0x007F or \
            (
                0x0030 <= code_unit <= 0x0039 and \
                    i == 0 or (i == 1 and css.startswith('-'))
            )
        ):
            result += f'\\{code_unit:x} '
        elif (
            code_unit >= 0x0080 or
            code_unit == 0x002D or
            code_unit == 0x005F or
            0x0030 <= code_unit <= 0x0039 or
            0x0041 <= code_unit <= 0x005A or
            0x0061 <= code_unit <= 0x007A
        ):
            result += css[i]
        else:
            result += f'\\{css[i]}'

    return result
